import html
import re
from pathlib import Path

import folium
import gspread
import pandas as pd
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
# Puoi aggiungere o modificare le tipologie qui.
# I nomi devono corrispondere alla colonna "Tipologia" del Google Sheet.
# ============================================================

STILE_TIPOLOGIE = {
    "Aperitivo": {"colore": "orange", "icona": "glass"},
    "aperitivo": {"colore": "orange", "icona": "glass"},
    "Mezzi pubblici": {"colore": "brown", "icona": "bus"},

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
    """
    Restituisce colore e icona in base alla tipologia.
    Se la tipologia non è presente nel dizionario, usa "Altro".
    """
    if pd.isna(tipologia):
        return STILE_TIPOLOGIE["Altro"]

    tipologia = str(tipologia).strip()
    return STILE_TIPOLOGIE.get(tipologia, STILE_TIPOLOGIE["Altro"])


def valore_testo(row, colonna, default=""):
    """
    Legge un valore da una riga pandas evitando errori su celle vuote.
    """
    if colonna not in row:
        return default

    valore = row[colonna]

    if pd.isna(valore):
        return default

    return str(valore).strip()


def normalizza_coordinate(serie):
    """
    Converte coordinate anche se nel foglio sono scritte con virgola italiana.
    Esempio: 39,2165 diventa 39.2165
    """
    return pd.to_numeric(
        serie.astype(str).str.replace(",", ".", regex=False),
        errors="coerce"
    )


def converti_url_drive(url):
    """
    Converte alcuni link Google Drive in link immagine diretto.

    Esempio:
    https://drive.google.com/file/d/ID_FILE/view?usp=sharing

    diventa:
    https://drive.google.com/uc?export=view&id=ID_FILE
    """
    if not url:
        return ""

    url = str(url).strip()

    match = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=view&id={file_id}"

    match = re.search(r"[?&]id=([^&]+)", url)
    if "drive.google.com" in url and match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=view&id={file_id}"

    return url


def crea_html_immagine(url_immagine):
    """
    Crea il blocco HTML dell'immagine nel popup.
    Se non c'è immagine, restituisce stringa vuota.
    """
    if not url_immagine:
        return ""

    url_immagine = converti_url_drive(url_immagine)
    url_immagine = html.escape(url_immagine, quote=True)

    return f"""
        <img
            src="{url_immagine}"
            style="
                width:100%;
                max-height:150px;
                object-fit:cover;
                border-radius:10px;
                margin-bottom:10px;
                display:block;
            "
            loading="lazy"
        >
    """


# ============================================================
# LETTURA DATI DA GOOGLE SHEET
# ============================================================

def carica_dati():
    """
    Legge i dati dal Google Sheet usando il service account.
    Il file credenziali.json viene creato da GitHub Actions.
    """
    if not PERCORSO_CREDENZIALI.exists():
        raise FileNotFoundError(
            "File credenziali.json non trovato. "
            "Su GitHub Actions verifica il secret GOOGLE_CREDENTIALS_JSON."
        )

    credenziali = Credentials.from_service_account_file(
        str(PERCORSO_CREDENZIALI),
        scopes=SCOPES
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
    """
    Controlla colonne, pulisce celle vuote e rimuove righe senza coordinate.
    """
    if df.empty:
        raise ValueError("Il Google Sheet è vuoto o non contiene dati leggibili.")

    df.columns = [str(col).strip() for col in df.columns]

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

    df["Nome luogo"] = df["Nome luogo"].fillna("").astype(str).str.strip()
    df["Tipologia"] = df["Tipologia"].fillna("Altro").astype(str).str.strip()
    df["Tipologia"] = df["Tipologia"].replace("", "Altro")

    df["Indirizzo"] = df["Indirizzo"].fillna("").astype(str).str.strip()
    df["Descrizione"] = df["Descrizione"].fillna("").astype(str).str.strip()
    df["URL immagine"] = df["URL immagine"].fillna("").astype(str).str.strip()

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
    """
    Crea il popup HTML per ogni marker.
    Include immagine, nome, tipologia, indirizzo e descrizione.
    """
    nome = html.escape(valore_testo(row, "Nome luogo"))
    tipologia = html.escape(valore_testo(row, "Tipologia", "Altro"))
    indirizzo = html.escape(valore_testo(row, "Indirizzo"))
    descrizione = html.escape(valore_testo(row, "Descrizione"))
    url_immagine = valore_testo(row, "URL immagine")

    stile = ottieni_stile(tipologia)
    colore_marker = stile["colore"]
    colore_hex = COLORI_HEX.get(colore_marker, "#808080")

    html_immagine = crea_html_immagine(url_immagine)

    html_popup = f"""
    <div style="
        font-family: Arial, sans-serif;
        width: 260px;
        max-width: 260px;
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

    iframe = folium.IFrame(html_popup, width=300, height=360)
    return folium.Popup(iframe, max_width=320)


# ============================================================
# GENERAZIONE MAPPA
# ============================================================

def genera_mappa(df):
    """
    Genera la mappa Folium e la salva come HTML.
    """
    centro_lat = df["Latitudine"].mean()
    centro_lng = df["Longitudine"].mean()

    mappa = folium.Map(
        location=[centro_lat, centro_lng],
        zoom_start=13,
        tiles="CartoDB positron"
    )

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
                prefix="fa"
            )
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
