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
TOP_N            = int(os.getenv("RANKING_TOP", "15"))        # nombre d'entreprises à afficher
CHECK_INTERVAL   = int(os.getenv("RANKING_INTERVAL", "60"))   # minutes entre chaque update
# ────────────────────────────────────────────────────────────

COLOR_GOLD   = 0xF1C40F
COLOR_ERROR  = 0xED4245

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

intents = discord.Intents.default()
client  = discord.Client(intents=intents)

ranking_message_id: int | None = None


# ─── SCRAPING ──────────────────────────────────────────────

def get_current_month_url() -> str:
    now = datetime.now()
    # URL format: /company_stats/COUNTRY/all/YEAR/MONTH/1/1/1
    # 1/1/1 = ETS2, Real, Monthly
    return f"https://trucksbook.eu/company_stats/FR/all/{now.year}/{now.month}/1/1/1"


def scrape_ranking() -> list[dict] | None:
    """
    Scrape le classement mensuel des entreprises françaises ETS2 Réel sur TrucksBook.
    Retourne une liste de dicts: [{rank, name, km, url}]
    """
    url = get_current_month_url()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERREUR SCRAPE] {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    # Structure TrucksBook: chaque entreprise est dans un bloc h3 (rang) + lien + km
    # On cherche les balises h3 contenant les rangs (1st, 2nd, 3rd, ...)
    # et les blocs km associés

    # Méthode: parser les blocs .stat-item ou la structure répétitive
    # Structure HTML: <div> <h3>Nom</h3> <strong>Σ X km</strong> <h3>Xème</h3> </div>
    # En pratique: les h3 alternent "rang" / "nom entreprise"
    # On cherche tous les éléments avec "km" + le lien entreprise

    # Récupérer tous les liens vers des entreprises
    company_links = soup.find_all("a", href=lambda h: h and "/company/" in h)
    
    # Récupérer tous les blocs de km (contiennent "Σ" et "km")
    km_blocks = []
    for strong in soup.find_all("strong"):
        text = strong.get_text(strip=True)
        if "km" in text:
            km_blocks.append(text)

    # Récupérer les rangs (1st, 2nd, 3rd...)
    rank_headings = []
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if text.endswith(("st", "nd", "rd", "th")):
            try:
                rank = int(''.join(filter(str.isdigit, text)))
                rank_headings.append(rank)
            except ValueError:
                pass

    # Associer nom + km + rang
    for i, link in enumerate(company_links[:TOP_N]):
        name = link.get_text(strip=True)
        company_url = "https://trucksbook.eu" + link["href"]
        km_text = km_blocks[i] if i < len(km_blocks) else "?"
        # Nettoyer: "Σ 1 234 567 km" → "1 234 567"
        km_clean = km_text.replace("Σ", "").replace("km", "").strip()
        rank = rank_headings[i] if i < len(rank_headings) else i + 1

        results.append({
            "rank": rank,
            "name": name,
            "km": km_clean,
            "url": company_url,
        })

    return results if results else None


# ─── EMBED ─────────────────────────────────────────────────

def build_ranking_embed(companies: list[dict]) -> discord.Embed:
    now = datetime.now()
    month_fr = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ][now.month]

    embed = discord.Embed(
        title       = f"🏆 Classement TrucksBook — Entreprises Françaises",
        description = f"**ETS2 · Réel (≤100 km/h) · {month_fr} {now.year}**\nTop {len(companies)} entreprises françaises ce mois-ci",
        color       = COLOR_GOLD,
        timestamp   = datetime.now(timezone.utc),
    )

    lines = []
    for c in companies:
        rank   = c["rank"]
        medal  = MEDALS.get(rank, f"`#{rank:>2}`")
        name   = c["name"][:30]  # tronquer les noms très longs
        km     = c["km"]
        lines.append(f"{medal} **[{name}]({c['url']})** — {km} km")

    # Discord limite les champs embed à 1024 caractères → découper par blocs de 10
    chunk_size = 10
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i:i + chunk_size]
        label = f"#{i+1} – #{min(i+chunk_size, len(lines))}" if i > 0 else f"Top {min(chunk_size, len(lines))}"
        embed.add_field(name=label, value="\n".join(chunk), inline=False)
    embed.set_footer(text=f"Source : trucksbook.eu • Mise à jour toutes les {CHECK_INTERVAL} min")
    return embed


# ─── TÂCHE PÉRIODIQUE ──────────────────────────────────────

@tasks.loop(minutes=CHECK_INTERVAL)
async def update_ranking():
    global ranking_message_id

    channel = client.get_channel(RANKING_CHANNEL)
    if channel is None:
        print("[WARN] RANKING_CHANNEL introuvable")
        return

    companies = scrape_ranking()

    if not companies:
        embed = discord.Embed(
            title       = "❌ Classement TrucksBook indisponible",
            description = "Impossible de récupérer le classement pour le moment.\nRéessai dans quelques minutes.",
            color       = COLOR_ERROR,
            timestamp   = datetime.now(timezone.utc),
        )
    else:
        embed = build_ranking_embed(companies)

    # Édite le message épinglé ou en crée un nouveau
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


# ─── ÉVÉNEMENTS ────────────────────────────────────────────

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


# ─── LANCEMENT ─────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN manquant dans .env")
    elif RANKING_CHANNEL == 0:
        print("❌ RANKING_CHANNEL_ID manquant dans .env")
    else:
        client.run(TOKEN)
