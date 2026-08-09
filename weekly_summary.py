"""
週末の振り返りサマリー スクリプト

data/ に保存された直近7日分の毎日のニュースログを読み込み、
Geminiに「今週の傾向・まとめ」を書かせてDiscordに送る。

必要な環境変数(GitHub Actionsの「Secrets」に設定します):
- GEMINI_API_KEY : Google AI StudioでもらったAPIキー
- DISCORD_WEBHOOK_URL : Discordのウェブフックの URL
"""

import glob
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

JST = timezone(timedelta(hours=9))
GEMINI_MODEL = "gemini-3.5-flash"


def load_week_logs() -> str:
    """直近7日分の data/*.md を新しい順に読み込んで結合する"""
    today = datetime.now(JST).date()
    texts = []
    for i in range(7):
        d = today - timedelta(days=i)
        path = f"data/{d.isoformat()}.md"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                texts.append(f.read())

    if not texts:
        raise RuntimeError("直近7日分のログが見つかりませんでした")
    return "\n\n---\n\n".join(texts)


def summarize_week(week_text: str, api_key: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )

    prompt = (
        "以下は、直近1週間分の毎日のニュースブリーフィングのログです。"
        "これを踏まえて『今週の振り返り』を作成してください。\n\n"
        "出力形式(Markdown、この構成を厳守):\n"
        "## 今週の大きな流れ\n"
        "(3〜5個の箇条書き。1週間を通して見えてきた大きな動き・繰り返し出てきたテーマ)\n\n"
        "## 分野別ハイライト\n"
        "(経済/健康科学/哲学・心理学/投資など、分野ごとに印象的だった話題を1〜2行で)\n\n"
        "## 来週チェックしたい観点\n"
        "(このまま追いかけると面白そうな論点を1〜3個)\n\n"
        "前置き・締めの挨拶は不要。この3見出し以外は出力しないこと。\n\n"
        f"{week_text}"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Geminiの応答を解析できませんでした: {data}") from exc


def send_to_discord(content: str, webhook_url: str) -> None:
    message = f"🗓️ **週末振り返りサマリー**\n\n{content}"
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

    week_text = load_week_logs()
    summary = summarize_week(week_text, gemini_key)
    send_to_discord(summary, discord_webhook)
    print("週末サマリーの送信が完了しました")


if __name__ == "__main__":
    main()
