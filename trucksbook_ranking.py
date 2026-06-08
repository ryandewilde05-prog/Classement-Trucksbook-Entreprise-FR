import discord
from discord.ext import tasks
import os
import subprocess
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────
TOKEN            = os.getenv("DISCORD_TOKEN")
RANKING_CHANNEL  = int(os.getenv("RANKING_CHANNEL_ID", "0"))
TOP_N            = int(os.getenv("RANKING_TOP", "15"))
CHECK_INTERVAL   = int(os.getenv("RANKING_INTERVAL", "60"))
# ────────────────────────────────────────────────────────────

COLOR_GOLD  = 0xF1C40F
COLOR_ERROR = 0xED4245
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

# Installer Chromium au démarrage si absent
print("[INIT] Installation de Chromium...")
subprocess.run(["playwright", "install", "chromium"], check=True)
print("[INIT] Chromium OK")

from playwright.async_api import async_playwright

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
ranking_message_id = None


# ─── SCRAPING ──────────────────────────────────────────────

async def scrape_ranking():
    now = datetime.now()
    url = f"https://trucksbook.eu/company_stats/all/fr/{now.year}/{now.month}/1/1/1"
    print(f"[SCRAPE] {url}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector("a[href*='/company/']", timeout=10000)

            results = await page.evaluate(f"""
                () => {{
                    const items = [];
                    const links = document.querySelectorAll('a[href*="/company/"]');
                    let rank = 1;
                    for (const link of links) {{
                        if (rank > {TOP_N}) break;
                        const name = link.textContent.trim();
                        const href = link.getAttribute('href');
                        let el = link.parentElement;
                        let km = '?';
                        for (let i = 0; i < 8; i++) {{
                            if (!el) break;
                            const strongs = el.querySelectorAll('strong');
                            for (const s of strongs) {{
                                const t = s.textContent.trim();
                                if (t.includes('km')) {{
                                    km = t.replace('Σ', '').replace('km', '').trim();
                                    break;
                                }}
                            }}
                            if (km !== '?') break;
                            el = el.parentElement;
                        }}
                        items.push({{ rank, name, km, url: 'https://trucksbook.eu' + href }});
                        rank++;
                    }}
                    return items;
                }}
            """)

            await browser.close()
            print(f"[SCRAPE] {len(results)} résultats — #1: {results[0] if results else 'vide'}")
            return results if results else None

    except Exception as e:
        print(f"[ERREUR SCRAPE] {e}")
        return None


# ─── EMBED ─────────────────────────────────────────────────

def build_embed(companies):
    now = datetime.now()
    months = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
              "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

    embed = discord.Embed(
        title       = "🏆 Classement TrucksBook — Entreprises Françaises",
        description = f"**ETS2 · Réel (≤100 km/h) · {months[now.month]} {now.year}**\nTop {len(companies)} entreprises françaises ce mois-ci",
        color       = COLOR_GOLD,
        timestamp   = datetime.now(timezone.utc),
    )

    lines = []
    for c in companies:
        rank  = c["rank"]
        medal = MEDALS.get(rank, f"`#{rank:>2}`")
        name  = c["name"][:32]
        lines.append(f"{medal} **[{name}]({c['url']})** — {c['km']} km")

    for i in range(0, len(lines), 10):
        chunk = lines[i:i+10]
        label = "Top 10" if i == 0 else f"#{i+1} – #{min(i+10, len(lines))}"
        embed.add_field(name=label, value="\n".join(chunk), inline=False)

    embed.set_footer(text=f"Source : trucksbook.eu • Mise à jour toutes les {CHECK_INTERVAL} min")
    return embed


# ─── TÂCHE PÉRIODIQUE ──────────────────────────────────────

@tasks.loop(minutes=CHECK_INTERVAL)
async def update_ranking():
    global ranking_message_id

    channel = client.get_channel(RANKING_CHANNEL)
    if channel is None:
        print("[WARN] Channel introuvable")
        return

    companies = await scrape_ranking()

    embed = build_embed(companies) if companies else discord.Embed(
        title       = "❌ Classement TrucksBook indisponible",
        description = "Impossible de récupérer le classement. Réessai dans quelques minutes.",
        color       = COLOR_ERROR,
        timestamp   = datetime.now(timezone.utc),
    )

    if ranking_message_id:
        try:
            msg = await channel.fetch_message(ranking_message_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            ranking_message_id = None

    msg = await channel.send(embed=embed)
    ranking_message_id = msg.id
    try:
        await msg.pin()
    except discord.Forbidden:
        pass


# ─── EVENTS ────────────────────────────────────────────────

@client.event
async def on_ready():
    print(f"✅ Classement bot connecté : {client.user}")
    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="classement FR TrucksBook 🏆"
        )
    )
    update_ranking.start()


if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN manquant dans .env")
    elif RANKING_CHANNEL == 0:
        print("❌ RANKING_CHANNEL_ID manquant dans .env")
    else:
        client.run(TOKEN)
