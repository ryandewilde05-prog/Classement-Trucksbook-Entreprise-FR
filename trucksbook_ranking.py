import discord
from discord.ext import tasks
import requests
from bs4 import BeautifulSoup
import os
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

intents = discord.Intents.default()
client  = discord.Client(intents=intents)

ranking_message_id = None


# ─── SCRAPING ──────────────────────────────────────────────

def get_url():
    now = datetime.now()
    # URL correcte confirmée : /company_stats/all/fr/YEAR/MONTH/1/1/1
    return f"https://trucksbook.eu/company_stats/all/fr/{now.year}/{now.month}/1/1/1"


def scrape_ranking():
    url = get_url()
    print(f"[SCRAPE] {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERREUR] {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    # Chaque entreprise = lien /company/ + strong avec km dans le même bloc parent
    company_links = soup.find_all("a", href=lambda h: h and "/company/" in h)
    print(f"[SCRAPE] {len(company_links)} liens trouvés")

    for i, link in enumerate(company_links[:TOP_N]):
        name = link.get_text(strip=True)
        company_url = "https://trucksbook.eu" + link["href"]

        # Chercher le km dans les balises strong proches
        km_clean = "?"
        parent = link.parent
        for _ in range(6):
            if parent is None:
                break
            strong = parent.find("strong")
            if strong:
                txt = strong.get_text(strip=True)
                if "km" in txt:
                    km_clean = txt.replace("Σ", "").replace("km", "").strip()
                    break
            parent = parent.parent

        results.append({
            "rank": i + 1,
            "name": name,
            "km": km_clean,
            "url": company_url,
        })

    if results:
        print(f"[SCRAPE] #1 → {results[0]}")
    return results if results else None


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

    # Découper par blocs de 10 (limite 1024 chars par champ Discord)
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

    companies = scrape_ranking()

    if not companies:
        embed = discord.Embed(
            title       = "❌ Classement TrucksBook indisponible",
            description = "Impossible de récupérer le classement. Réessai dans quelques minutes.",
            color       = COLOR_ERROR,
            timestamp   = datetime.now(timezone.utc),
        )
    else:
        embed = build_embed(companies)

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


# ─── MAIN ──────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN manquant dans .env")
    elif RANKING_CHANNEL == 0:
        print("❌ RANKING_CHANNEL_ID manquant dans .env")
    else:
        client.run(TOKEN)
