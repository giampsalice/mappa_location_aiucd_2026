from pathlib import Path
from html import escape
from urllib.parse import urlparse, parse_qs
import sys

import folium
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

# === CONFIGURAZIONE ===
BASE_DIR = Path(__file__).resolve().parent
PERCORSO_CREDENZIALI = "credenziali.json"
FOGLIO_ID = "170qWCxkWG8L3SzniqUIlXyegPRKvHf5g4f6Pe7Cj8xE"
NOME_TAB = "MAPPA"
FILE_OUTPUT = "mappa_location.html"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

STILE_TIPOLOGIE = {
    "Aperitivo": {"colore": "orange", "icona": "glass-martini"},
    "Culturale": {"colore": "purple", "icona": "landmark"},
    "Museo o visita culturale": {"colore": "purple", "icona": "landmark"},
    "Ristorante": {"colore": "red", "icona": "utensils"},
    "Bar": {"colore": "beige", "icona": "coffee"},
    "Punto panoramico": {"colore": "blue", "icona": "binoculars"},
    "Belvedere / aperitivi": {"colore": "orange", "icona": "binoculars"},
    "Passeggiata | Aperitivi": {"colore": "orange", "icona": "person-walking"},
    "Naturalistico": {"colore": "green", "icona": "tree"},
    "Shopping": {"colore": "pink", "icona": "shopping-bag"},
    "Hotel": {"colore": "darkblue", "icona": "bed"},
    "Sede del Convegno": {"colore": "darkred", "icona": "building"},
    "Altro": {"colore": "gray", "icona": "map-marker-alt"},
}

COLONNE_OBBLIGATORIE = [
    "Nome luogo",
    "Tipologia",
    "Indirizzo",
    "Descrizione",
    "Latitudine",
    "Longitudine",
]

# La colonna immagine e' opzionale. Puoi chiamarla in uno di questi modi.
COLONNE_URL_IMMAGINE = [
    "URL immagine",
    "Url immagine",
    "url immagine",
    "Immagine",
    "URL Immagine",
]


def normalizza_testo(valore):
    if pd.isna(valore):
        return ""
    return str(valore).strip()


def ottieni_stile(tipologia):
    tipologia = normalizza_testo(tipologia) or "Altro"
    return STILE_TIPOLOGIE.get(tipologia, STILE_TIPOLOGIE["Altro"])


def trova_colonna_immagine(df):
    for nome_colonna in COLONNE_URL_IMMAGINE:
        if nome_colonna in df.columns:
            return nome_colonna
    return None


def normalizza_url_immagine(url):
    """Restituisce un URL utilizzabile dentro un tag <img>.

    Nota: le immagini devono essere pubbliche. Se usi Google Drive, condividi
    l'immagine con 'Chiunque abbia il link' e incolla il link nel foglio.
    """
    url = normalizza_testo(url)
    if not url:
        return ""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    # Converte link Google Drive del tipo:
    # https://drive.google.com/file/d/FILE_ID/view?usp=sharing
    if "drive.google.com" in parsed.netloc and "/file/d/" in parsed.path:
        parti = parsed.path.split("/")
        try:
            file_id = parti[parti.index("d") + 1]
            return f"https://drive.google.com/uc?export=view&id={file_id}"
        except (ValueError, IndexError):
            return url

    # Converte link Google Drive del tipo:
    # https://drive.google.com/open?id=FILE_ID
    if "drive.google.com" in parsed.netloc:
        file_id = parse_qs(parsed.query).get("id", [""])[0]
        if file_id:
            return f"https://drive.google.com/uc?export=view&id={file_id}"

    return url


def carica_dati():
    if not PERCORSO_CREDENZIALI.exists():
        raise FileNotFoundError(
            f"Non trovo il file credenziali: {PERCORSO_CREDENZIALI}\n"
            "Metti credenziali.json nella stessa cartella dello script."
        )

    credenziali = Credentials.from_service_account_file(
        PERCORSO_CREDENZIALI,
        scopes=SCOPES,
    )
    client = gspread.authorize(credenziali)
    worksheet = client.open_by_key(FOGLIO_ID).worksheet(NOME_TAB)
    dati = worksheet.get_all_records()
    df = pd.DataFrame(dati)
    print(f"Caricati {len(df)} record dal foglio Google.")
    return df


def controlla_colonne(df):
    df.columns = [str(col).strip() for col in df.columns]
    mancanti = [col for col in COLONNE_OBBLIGATORIE if col not in df.columns]
    if mancanti:
        raise ValueError(
            "Nel foglio mancano queste colonne: " + ", ".join(mancanti) + "\n"
            "Colonne trovate: " + ", ".join(df.columns)
        )
    return df


def pulisci_dati(df):
    df = controlla_colonne(df).copy()

    colonna_immagine = trova_colonna_immagine(df)
    if colonna_immagine:
        print(f"Colonna immagini trovata: {colonna_immagine}")
    else:
        print("Colonna immagini non trovata: i popup saranno senza immagine.")

    for col in ["Nome luogo", "Tipologia", "Indirizzo", "Descrizione"]:
        df[col] = df[col].apply(normalizza_testo)

    if colonna_immagine:
        df["URL immagine"] = df[colonna_immagine].apply(normalizza_url_immagine)
    else:
        df["URL immagine"] = ""

    df = df[df["Nome luogo"] != ""]

    for col in ["Latitudine", "Longitudine"]:
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .str.replace(",", ".", regex=False)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    righe_senza_coordinate = df[df[["Latitudine", "Longitudine"]].isna().any(axis=1)]
    if not righe_senza_coordinate.empty:
        print("Righe ignorate perche' senza coordinate valide:")
        print(righe_senza_coordinate[["Nome luogo", "Latitudine", "Longitudine"]].to_string(index=False))

    df = df.dropna(subset=["Latitudine", "Longitudine"])
    df.loc[df["Tipologia"] == "", "Tipologia"] = "Altro"

    print(f"{len(df)} location valide dopo la pulizia.")
    return df


def crea_popup(row):
    stile = ottieni_stile(row["Tipologia"])
    colori_hex = {
        "orange": "#FF8C00",
        "purple": "#8B008B",
        "red": "#DC143C",
        "beige": "#D2691E",
        "blue": "#1E90FF",
        "green": "#228B22",
        "pink": "#FF69B4",
        "darkblue": "#00008B",
        "darkred": "#8B0000",
        "gray": "#808080",
    }
    colore = colori_hex.get(stile["colore"], "#808080")

    nome = escape(normalizza_testo(row["Nome luogo"]))
    tipologia = escape(normalizza_testo(row["Tipologia"]))
    indirizzo = escape(normalizza_testo(row["Indirizzo"]))
    descrizione = escape(normalizza_testo(row["Descrizione"]))
    url_immagine = normalizza_testo(row.get("URL immagine", ""))

    immagine_html = ""
    if url_immagine:
        immagine_sicura = escape(url_immagine, quote=True)
        alt_sicuro = escape(f"Immagine di {nome}", quote=True)
        immagine_html = f"""
        <img src="{immagine_sicura}" alt="{alt_sicuro}"
             style="width:100%;height:130px;object-fit:cover;border-radius:10px;margin:0 0 9px 0;display:block;"
             referrerpolicy="no-referrer"
             onerror="this.style.display='none';">
        """

    html = f"""
    <div style="font-family:Arial,sans-serif;min-width:240px;max-width:320px;">
        {immagine_html}
        <h3 style="margin:0 0 6px;color:{colore};font-size:16px;line-height:1.2;">{nome}</h3>
        <span style="background:{colore};color:white;padding:2px 8px;border-radius:12px;font-size:11px;display:inline-block;">{tipologia}</span>
        <hr style="margin:8px 0;border:none;border-top:1px solid #eee;">
        <p style="margin:4px 0;font-size:12px;color:#555;line-height:1.35;">📍 {indirizzo}</p>
        <p style="margin:6px 0 0;font-size:13px;color:#333;line-height:1.35;">{descrizione}</p>
    </div>
    """
    return folium.Popup(folium.IFrame(html, width=350, height=330), max_width=350)


def genera_mappa(df):
    if df.empty:
        raise ValueError("Nessuna location valida: controlla che latitudine e longitudine siano compilate.")

    centro_lat = df["Latitudine"].mean()
    centro_lng = df["Longitudine"].mean()

    mappa = folium.Map(
        location=[centro_lat, centro_lng],
        zoom_start=13,
        tiles="CartoDB positron",
        control_scale=True,
    )

    gruppi = {}
    for tipologia in sorted(df["Tipologia"].unique()):
        gruppo = folium.FeatureGroup(name=tipologia, show=True)
        gruppi[tipologia] = gruppo
        gruppo.add_to(mappa)

    for _, row in df.iterrows():
        stile = ottieni_stile(row["Tipologia"])
        folium.Marker(
            location=[row["Latitudine"], row["Longitudine"]],
            popup=crea_popup(row),
            tooltip=f"{row['Nome luogo']} ({row['Tipologia']})",
            icon=folium.Icon(
                color=stile["colore"],
                icon=stile["icona"],
                prefix="fa",
            ),
        ).add_to(gruppi.get(row["Tipologia"], mappa))

    folium.LayerControl(collapsed=False).add_to(mappa)
    mappa.save(str(FILE_OUTPUT))
    print(f"Mappa salvata: {FILE_OUTPUT}")


def main():
    try:
        df = carica_dati()
        df = pulisci_dati(df)
        genera_mappa(df)
    except Exception as errore:
        print("ERRORE:", errore, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
