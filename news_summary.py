"""
毎朝のニュース自動要約 → Discord通知 スクリプト

やっていること：
1. Yahoo!ニュースの「主要トピックス」RSS(個人利用の範囲)を取得
2. 各記事のタイトルをGemini APIでまとめて要約
3. できあがった要約をDiscordのWebhookに送信

必要な環境変数(GitHub Actionsの「Secrets」に設定します):
- GEMINI_API_KEY : Google AI StudioでもらったAPIキー
- DISCORD_WEBHOOK_URL : Discordのウェブフックの URL
"""

import os
import sys
import feedparser
import requests

# --- 設定 ---------------------------------------------------------------
RSS_URL = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"  # 個人利用のみ
MAX_ARTICLES = 8  # 要約に使う記事数(多すぎるとAPIが重くなるので絞る)
GEMINI_MODEL = "gemini-3.5-flash"  # 無料枠で使える標準モデル(2026年8月時点)


def get_headlines() -> list[str]:
    """RSSから記事タイトルを取得する"""
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        raise RuntimeError("RSSからニュースを取得できませんでした")
    return [entry.title for entry in feed.entries[:MAX_ARTICLES]]


def summarize_with_gemini(headlines: list[str], api_key: str) -> str:
    """Gemini APIでニュース一覧を要約する"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )

    news_list = "\n".join(f"- {title}" for title in headlines)
    prompt = (
        "以下は今日の主要ニュースの見出し一覧です。"
        "日本語で、要点をまとめて5行以内の箇条書きで簡潔に要約してください。"
        "見出しをそのまま並べるのではなく、内容がひと目で分かるようにしてください。\n\n"
        f"{news_list}"
    )

    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Geminiの応答を解析できませんでした: {data}") from exc


def send_to_discord(message: str, webhook_url: str) -> None:
    """Discordのウェブフックにメッセージを送信する"""
    # Discordの1メッセージは2000文字制限があるので念のため切る
    content = message[:1900]
    response = requests.post(webhook_url, json={"content": content}, timeout=30)
    response.raise_for_status()


def main() -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    if not gemini_key or not discord_webhook:
        print("環境変数 GEMINI_API_KEY / DISCORD_WEBHOOK_URL が設定されていません", file=sys.stderr)
        sys.exit(1)

    headlines = get_headlines()
    summary = summarize_with_gemini(headlines, gemini_key)

    message = f"📰 **今日のニュースまとめ**\n\n{summary}"
    send_to_discord(message, discord_webhook)
    print("Discordへの送信が完了しました")


if __name__ == "__main__":
    main()
