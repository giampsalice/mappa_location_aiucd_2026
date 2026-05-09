import base64
import html
import re
from pathlib import Path

import folium
import gspread
import pandas as pd
import requests
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURAZIONE
# ============================================================

PERCORSO_CREDENZIALI = Path("credenziali.json")

FOGLIO_ID = "170qWCxkWG8L3SzniqUIlXyegPRKvHf5g4f6Pe7Cj8xE"
NOME_TAB = "MAPPA"

FILE_OUTPUT = "mappa_location.html"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ============================================================
# STILI MARKER PER TIPOLOGIA
# ============================================================

STILE_TIPOLOGIE = {
    "Aperitivo": {"colore": "orange", "icona": "glass"},
    "aperitivo": {"colore": "orange", "icona": "glass"},

    "Museo o visita culturale": {"colore": "purple", "icona": "university"},
    "Culturale": {"colore": "purple", "icona": "university"},

    "Ristorante": {"colore": "red", "icona": "cutlery"},
    "Bar": {"colore": "beige", "icona": "coffee"},

    "Punto panoramico": {"colore": "blue", "icona": "binoculars"},
    "Belvedere / aperitivi": {"colore": "blue", "icona": "binoculars"},

    "Passeggiata | Aperitivi": {"colore": "cadetblue", "icona": "road"},

    "Naturalistico": {"colore": "green", "icona": "tree"},

    "Shopping": {"colore": "pink", "icona": "shopping-bag"},
    "Hotel": {"colore": "darkblue", "icona": "bed"},

    "Sede del Convegno": {"colore": "darkred", "icona": "building"},

    "Mezzi pubblici": {"colore": "green", "icona": "bus"},

    "Altro": {"colore": "gray", "icona": "map-marker"},
}


COLORI_HEX = {
    "orange": "#FF8C00",
    "purple": "#8B008B",
    "red": "#DC143C",
    "beige": "#D2691E",
    "blue": "#1E90FF",
    "cadetblue": "#5F9EA0",
    "green": "#228B22",
    "pink": "#FF69B4",
    "darkblue": "#00008B",
    "darkred": "#8B0000",
    "gray": "#808080",
}


# ============================================================
# FUNZIONI DI SUPPORTO
# ============================================================

def ottieni_stile(tipologia):
    if pd.isna(tipologia):
        return STILE_TIPOLOGIE["Altro"]

    tipologia = str(tipologia).strip()
    return STILE_TIPOLOGIE.get(tipologia, STILE_TIPOLOGIE["Altro"])


def valore_testo(row, colonna, default=""):
    if colonna not in row:
        return default

    valore = row[colonna]

    if pd.isna(valore):
        return default

    return str(valore).strip()


def normalizza_coordinate(serie):
    return pd.to_numeric(
        serie.astype(str).str.replace(",", ".", regex=False),
        errors="coerce"
    )


def normalizza_colonne(df):
    """
    Normalizza i nomi delle colonne del Google Sheet.
    Gestisce varianti tipo:
    - URL immagine
    - url immagine
    - link foto
    - immagine
    - image
    - photo
    """
    df.columns = [str(col).strip() for col in df.columns]

    mappa_colonne = {}

    for colonna in df.columns:
        nome_normale = (
            str(colonna)
            .strip()
            .lower()
            .replace("\n", " ")
            .replace("\r", " ")
            .replace("\t", " ")
        )

        nome_normale = " ".join(nome_normale.split())

        if (
            "immagine" in nome_normale
            or "immagini" in nome_normale
            or "image" in nome_normale
            or "foto" in nome_normale
            or "photo" in nome_normale
        ):
            mappa_colonne[colonna] = "URL immagine"

        elif nome_normale in ["nome luogo", "nome", "luogo", "name", "place"]:
            mappa_colonne[colonna] = "Nome luogo"

        elif nome_normale in ["tipologia", "tipo", "categoria", "category", "type"]:
            mappa_colonne[colonna] = "Tipologia"

        elif nome_normale in ["indirizzo", "address"]:
            mappa_colonne[colonna] = "Indirizzo"

        elif nome_normale in ["descrizione", "description", "desc"]:
            mappa_colonne[colonna] = "Descrizione"

        elif nome_normale in ["latitudine", "lat", "latitude"]:
            mappa_colonne[colonna] = "Latitudine"

        elif nome_normale in ["longitudine", "lon", "lng", "longitude"]:
            mappa_colonne[colonna] = "Longitudine"

    print("Mappa colonne applicata:", mappa_colonne)

    return df.rename(columns=mappa_colonne)


def sembra_url_immagine(valore):
    """
    Riconosce se una cella contiene un URL plausibile di immagine.
    Serve come fallback se il nome della colonna immagini non viene riconosciuto.
    """
    if pd.isna(valore):
        return False

    testo = str(valore).strip().lower()

    if not testo.startswith("http"):
        return False

    indicatori = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        "wikimedia.org",
        "googleusercontent.com",
        "sardegnaturismo.it",
        "tripadvisor.com",
        "ytimg.com",
        "cagliariturismo",
        "sardegnacultura",
        "citynews",
    ]

    return any(indicatore in testo for indicatore in indicatori)


def trova_colonna_immagini_automaticamente(df):
    """
    Cerca automaticamente la colonna che contiene più URL immagine.
    """
    migliore_colonna = None
    massimo_url = 0

    for colonna in df.columns:
        conteggio = df[colonna].apply(sembra_url_immagine).sum()

        print(f"DEBUG colonna '{colonna}': {conteggio} possibili URL immagine")

        if conteggio > massimo_url:
            massimo_url = conteggio
            migliore_colonna = colonna

    if migliore_colonna and massimo_url > 0:
        print(f"Colonna immagini trovata automaticamente: {migliore_colonna}")
        return migliore_colonna

    print("Nessuna colonna immagini trovata automaticamente.")
    return None


def converti_url_drive(url):
    """
    Converte eventuali link Google Drive in URL thumbnail.
    Se l'URL non è Google Drive, lo lascia invariato.
    """
    if not url:
        return ""

    url = str(url).strip()

    if "drive.google.com" not in url:
        return url

    file_id = ""

    match = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if match:
        file_id = match.group(1)

    if not file_id:
        match = re.search(r"[?&]id=([^&]+)", url)
        if match:
            file_id = match.group(1)

    if file_id:
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

    return url


def scarica_immagine_base64(url_immagine, nome_luogo="immagine"):
    """
    Scarica l'immagine e la incorpora in base64.
    In questo modo l'immagine finisce direttamente dentro mappa_location.html.
    """
    if not url_immagine:
        return ""

    url_finale = converti_url_drive(url_immagine)

    try:
        response = requests.get(
            url_finale,
            timeout=30,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )

        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        print("---- DEBUG IMMAGINE ----")
        print(f"Luogo: {nome_luogo}")
        print(f"URL originale: {url_immagine}")
        print(f"URL finale usato: {url_finale}")
        print(f"HTTP status: {response.status_code}")
        print(f"Content-Type ricevuto: {content_type}")
        print(f"Dimensione contenuto: {len(response.content)} byte")

        if "image" not in content_type:
            print(f"ATTENZIONE: URL non sembra essere un'immagine per {nome_luogo}")
            return ""

        immagine_base64 = base64.b64encode(response.content).decode("utf-8")

        print(f"Immagine incorporata correttamente per: {nome_luogo}")

        return f"data:{content_type};base64,{immagine_base64}"

    except Exception as e:
        print("---- ERRORE IMMAGINE ----")
        print(f"Luogo: {nome_luogo}")
        print(f"URL originale: {url_immagine}")
        print(f"URL finale usato: {url_finale}")
        print(e)
        return ""


def crea_html_immagine(url_immagine, nome_luogo="immagine"):
    """
    Crea il blocco HTML dell'immagine nel popup.
    """
    if not url_immagine:
        return ""

    src_immagine = scarica_immagine_base64(url_immagine, nome_luogo)

    if not src_immagine:
        return ""

    src_immagine = html.escape(src_immagine, quote=True)

    return f"""
        <img
            src="{src_immagine}"
            style="
                width:100%;
                max-height:180px;
                object-fit:cover;
                border-radius:10px;
                margin-bottom:10px;
                display:block;
                background:#eeeeee;
            "
            loading="lazy"
            onerror="this.style.display='none';"
        >
    """


# ============================================================
# LETTURA DATI DA GOOGLE SHEET
# ============================================================

def carica_dati():
    if not Path(PERCORSO_CREDENZIALI).exists():
        raise FileNotFoundError(
            "File credenziali.json non trovato. "
            "Su GitHub Actions verifica il secret GOOGLE_CREDENTIALS_JSON."
        )

    credenziali = Credentials.from_service_account_file(
        str(PERCORSO_CREDENZIALI),
        scopes=SCOPES,
    )

    client = gspread.authorize(credenziali)
    foglio = client.open_by_key(FOGLIO_ID).worksheet(NOME_TAB)

    dati = foglio.get_all_records()
    df = pd.DataFrame(dati)

    print(f"Caricati {len(df)} record dal foglio Google.")
    print("Colonne trovate:", list(df.columns))

    return df


# ============================================================
# PULIZIA DATI
# ============================================================

def pulisci_dati(df):
    if df.empty:
        raise ValueError("Il Google Sheet è vuoto o non contiene dati leggibili.")

    df = normalizza_colonne(df)

    print("Colonne dopo normalizzazione:", list(df.columns))

    print("DEBUG colonne con repr:")
    for col in df.columns:
        print(repr(col))

    print("DEBUG prime 3 righe:")
    print(df.head(3).to_string())

    colonne_obbligatorie = [
        "Nome luogo",
        "Tipologia",
        "Latitudine",
        "Longitudine",
    ]

    colonne_mancanti = [
        colonna for colonna in colonne_obbligatorie
        if colonna not in df.columns
    ]

    if colonne_mancanti:
        raise ValueError(
            "Mancano queste colonne obbligatorie nel Google Sheet: "
            + ", ".join(colonne_mancanti)
        )

    colonne_facoltative = [
        "Indirizzo",
        "Descrizione",
        "URL immagine",
    ]

    for colonna in colonne_facoltative:
        if colonna not in df.columns:
            df[colonna] = ""

    # Fallback: se la colonna URL immagine è vuota o non riconosciuta,
    # prova a trovare automaticamente la colonna che contiene URL immagine.
    colonna_auto_immagini = trova_colonna_immagini_automaticamente(df)

    if colonna_auto_immagini:
        df["URL immagine"] = df[colonna_auto_immagini]

    df["Nome luogo"] = df["Nome luogo"].fillna("").astype(str).str.strip()
    df["Tipologia"] = df["Tipologia"].fillna("Altro").astype(str).str.strip()
    df["Tipologia"] = df["Tipologia"].replace("", "Altro")

    df["Indirizzo"] = df["Indirizzo"].fillna("").astype(str).str.strip()
    df["Descrizione"] = df["Descrizione"].fillna("").astype(str).str.strip()
    df["URL immagine"] = df["URL immagine"].fillna("").astype(str).str.strip()

    print("URL immagini non vuoti:", (df["URL immagine"] != "").sum())
    print("Primi URL immagine:", df["URL immagine"].head(10).tolist())

    df["Latitudine"] = normalizza_coordinate(df["Latitudine"])
    df["Longitudine"] = normalizza_coordinate(df["Longitudine"])

    prima = len(df)

    df = df[df["Nome luogo"] != ""]
    df = df.dropna(subset=["Latitudine", "Longitudine"])

    dopo = len(df)

    print(f"Location valide dopo la pulizia: {dopo}")
    print(f"Righe ignorate perché vuote o senza coordinate: {prima - dopo}")

    if dopo == 0:
        raise ValueError(
            "Nessuna location valida trovata. "
            "Controlla che Nome luogo, Latitudine e Longitudine siano compilati."
        )

    return df


# ============================================================
# POPUP
# ============================================================

def crea_popup(row):
    nome_raw = valore_testo(row, "Nome luogo")
    tipologia_raw = valore_testo(row, "Tipologia", "Altro")
    indirizzo_raw = valore_testo(row, "Indirizzo")
    descrizione_raw = valore_testo(row, "Descrizione")
    url_immagine = valore_testo(row, "URL immagine")

    nome = html.escape(nome_raw)
    tipologia = html.escape(tipologia_raw)
    indirizzo = html.escape(indirizzo_raw)
    descrizione = html.escape(descrizione_raw)

    stile = ottieni_stile(tipologia_raw)
    colore_marker = stile["colore"]
    colore_hex = COLORI_HEX.get(colore_marker, "#808080")

    html_immagine = crea_html_immagine(url_immagine, nome_raw)

    html_popup = f"""
    <div style="
        font-family: Arial, sans-serif;
        width: 270px;
        max-width: 270px;
    ">
        {html_immagine}

        <h3 style="
            margin: 0 0 6px 0;
            color: {colore_hex};
            font-size: 17px;
            line-height: 1.2;
        ">
            {nome}
        </h3>

        <span style="
            background: {colore_hex};
            color: white;
            padding: 3px 9px;
            border-radius: 12px;
            font-size: 11px;
            display: inline-block;
            margin-bottom: 8px;
        ">
            {tipologia}
        </span>

        <hr style="
            margin: 8px 0;
            border: none;
            border-top: 1px solid #eeeeee;
        ">

        <p style="
            margin: 4px 0;
            font-size: 12px;
            color: #555555;
        ">
            📍 {indirizzo}
        </p>

        <p style="
            margin: 8px 0 0 0;
            font-size: 13px;
            color: #333333;
            line-height: 1.35;
        ">
            {descrizione}
        </p>
    </div>
    """

    iframe = folium.IFrame(html_popup, width=320, height=430)
    return folium.Popup(iframe, max_width=340)


# ============================================================
# GENERAZIONE MAPPA
# ============================================================

def genera_mappa(df):
    centro_lat = df["Latitudine"].mean()
    centro_lng = df["Longitudine"].mean()

    # Mappa chiara impostata come layer di default.
    mappa = folium.Map(
        location=[centro_lat, centro_lng],
        zoom_start=13,
        tiles="CartoDB positron",
    )

    # Layer alternativi selezionabili.
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap",
        control=True,
        show=False,
    ).add_to(mappa)

    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Mappa scura",
        control=True,
        show=False,
    ).add_to(mappa)

    gruppi = {}

    for tipologia in sorted(df["Tipologia"].dropna().unique()):
        gruppo = folium.FeatureGroup(name=str(tipologia), show=True)
        gruppi[tipologia] = gruppo
        gruppo.add_to(mappa)

    for _, row in df.iterrows():
        tipologia = valore_testo(row, "Tipologia", "Altro")
        stile = ottieni_stile(tipologia)

        nome = valore_testo(row, "Nome luogo")
        tooltip = f"{nome} ({tipologia})"

        marker = folium.Marker(
            location=[row["Latitudine"], row["Longitudine"]],
            popup=crea_popup(row),
            tooltip=tooltip,
            icon=folium.Icon(
                color=stile["colore"],
                icon=stile["icona"],
                prefix="fa",
            ),
        )

        if tipologia in gruppi:
            marker.add_to(gruppi[tipologia])
        else:
            marker.add_to(mappa)

    folium.LayerControl(collapsed=False).add_to(mappa)

    mappa.save(FILE_OUTPUT)

    print(f"Mappa salvata correttamente come {FILE_OUTPUT}")


# ============================================================
# AVVIO SCRIPT
# ============================================================

def main():
    df = carica_dati()
    df = pulisci_dati(df)
    genera_mappa(df)


if __name__ == "__main__":
    main()
