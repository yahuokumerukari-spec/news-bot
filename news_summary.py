"""
毎朝のニュース自動要約 → Discord通知 スクリプト

やっていること：
1. Yahoo!ニュースの複数カテゴリRSS＋自分の関心分野のGoogleニュース検索(個人利用の範囲)を取得
   ("主要"カテゴリを必ず含めることで、その日絶対外せない大きな
   ニュースを取りこぼさないようにしている。芸能・スポーツは除外)
2. 記事のタイトル+概要をGemini APIに渡し、要点を押さえたブリーフィングを作らせる
3. できあがった要約を
   - リポジトリの data/YYYY-MM-DD.md に保存(週末サマリー用のログ)
   - Discordに通知
   する

必要な環境変数(GitHub Actionsの「Secrets」に設定します):
- GEMINI_API_KEY : Google AI StudioでもらったAPIキー
- DISCORD_WEBHOOK_URL : Discordのウェブフックの URL
"""

import os
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser
import requests

# --- 設定 ---------------------------------------------------------------
JST = timezone(timedelta(hours=9))


def google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=ja&gl=JP&ceid=JP:ja"


# 「主要」は絶対外せない大きなニュースを拾うための保険枠。
# 芸能(entertainment)・スポーツ(sports)は意図的に含めていない。
RSS_FEEDS = {
    "主要": "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
    "経済": "https://news.yahoo.co.jp/rss/topics/business.xml",
    "国際": "https://news.yahoo.co.jp/rss/topics/world.xml",
    "IT・科学": "https://news.yahoo.co.jp/rss/topics/it.xml",
    "金融リテラシー・投資": google_news_url("金融リテラシー OR 資産形成 OR 投資 初心者"),
    "健康科学(睡眠・食事・運動)": google_news_url("睡眠 OR 食事 OR 運動 研究"),
    "哲学・心理学": google_news_url("哲学 OR 心理学"),
}
MAX_ARTICLES_PER_FEED = 5  # 各フィードから使う記事数(分野が増えたので少し絞る)
GEMINI_MODEL = "gemini-3.5-flash"  # 無料枠で使える標準モデル(2026年8月時点)


def get_articles() -> list[dict]:
    """複数のRSSから記事(タイトル+概要)を取得する(タイトル重複は除去)"""
    articles = []
    seen_titles = set()

    for category, url in RSS_FEEDS.items():
        feed = feedparser.parse(url)
        if not feed.entries:
            continue
        for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
            if entry.title in seen_titles:
                continue
            seen_titles.add(entry.title)
            articles.append(
                {
                    "category": category,
                    "title": entry.title,
                    "summary": getattr(entry, "summary", ""),
                }
            )

    if not articles:
        raise RuntimeError("RSSからニュースを取得できませんでした")
    return articles


def summarize_with_gemini(articles: list[dict], api_key: str) -> str:
    """Gemini APIでニュース一覧を要約する"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )

    news_list = "\n".join(
        f"[{a['category']}] {a['title']} — {a['summary']}".strip(" —")
        for a in articles
    )

    prompt = (
        "あなたは、読者(ビジネス・投資・健康科学・哲学/心理学・プログラミングに関心がある社会人)"
        "向けの朝刊編集者です。"
        "以下は複数分野のニュース見出しと概要です。これをもとに、"
        "毎朝読むだけで『今日押さえておくべき情報』が分かるブリーフィングを作成してください。\n\n"
        "条件:\n"
        "- [主要]タグが付いている記事は、その日の重大ニュースである可能性が高いので"
        "できる限り優先的に含めること(内容が薄い・重複していると判断した場合のみ除外可)\n"
        "- 芸能人のゴシップ・恋愛・不倫などの芸能ネタは完全に除外する\n"
        "- 各分野(主要/経済/国際/IT・科学/金融リテラシー・投資/健康科学/哲学・心理学)から"
        "バランスよく、全体で8〜12トピックを選ぶ(重複や瑣末なネタは除く)\n"
        "- 1トピックにつき1〜2文で「何が起きたか」＋「なぜ重要か/学び」を書く"
        "(見出しの言い換えだけで終わらせない)\n"
        "- 各トピックの先頭に元の分野タグ(例：【主要】【経済】【健康科学】【哲学・心理学】など)をつける\n"
        "- 箇条書き形式、日本語、専門用語を使ってよいが簡潔に\n"
        "- 前置きや締めの挨拶文は不要。箇条書き本体のみ出力する\n\n"
        f"{news_list}"
    )

    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Geminiの応答を解析できませんでした: {data}") from exc


def save_log(date_str: str, content: str) -> str:
    """週末サマリーで振り返れるように、毎日の要約をリポジトリに保存する"""
    os.makedirs("data", exist_ok=True)
    path = f"data/{date_str}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# ニュースブリーフィング {date_str}\n\n{content}\n")
    return path


def send_to_discord(message: str, webhook_url: str) -> None:
    """Discordのウェブフックにメッセージを送信する(2000文字制限があるので分割送信)"""
    chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)] or [message]
    for chunk in chunks:
        response = requests.post(webhook_url, json={"content": chunk}, timeout=30)
        response.raise_for_status()


def main() -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    if not gemini_key or not discord_webhook:
        print("環境変数 GEMINI_API_KEY / DISCORD_WEBHOOK_URL が設定されていません", file=sys.stderr)
        sys.exit(1)

    date_str = datetime.now(JST).strftime("%Y-%m-%d")
    articles = get_articles()
    summary = summarize_with_gemini(articles, gemini_key)

    save_log(date_str, summary)

    message = f"📰 **今日のニュースブリーフィング**\n\n{summary}"
    send_to_discord(message, discord_webhook)
    print("Discordへの送信が完了しました")


if __name__ == "__main__":
    main()
