"""
Filtro de exclusión de grandes cadenas, franquicias y corporaciones en BuscaLeads.

Garantiza que la prospección comercial se enfoque exclusivamente en negocios
locales independientes y pymes de barrio (el público objetivo de páginas web,
SEO local y tarjetas NFC de FACENIA), descartando grandes superficies,
banca, supermercados y cadenas multinacionales centralizadas.
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

# Tipos de Google Places y OpenStreetMap que representan grandes corporaciones o infraestructuras
EXCLUDED_PLACE_TYPES: Set[str] = {
    # Comercio a gran escala
    "supermarket",
    "hypermarket",
    "department_store",
    "shopping_mall",
    "wholesale_store",
    "warehouse_store",
    # Banca y finanzas corporativas
    "bank",
    "atm",
    # Combustibles y áreas de servicio
    "gas_station",
    # Cadenas de transporte / infraestructuras
    "airport",
    "train_station",
    "bus_station",
    "subway_station",
}

# Lista exhaustiva de grandes marcas, franquicias y cadenas en España
KNOWN_LARGE_CHAINS: Set[str] = {
    # Cadenas de comida rápida y restauración organizada
    "mcdonald", "mcdonalds", "mcdonald's", "burger king", "kfc", "kentucky fried chicken",
    "subway", "domino's", "dominos pizza", "telepizza", "starbucks", "taco bell",
    "popeyes", "dunkin", "dunkin coffee", "100 montaditos", "cien montaditos", "tgb",
    "the good burger", "la sureña", "sureña", "foster's hollywood", "fosters hollywood",
    "goiko", "goiko grill", "vips", "vips smart", "ginos", "la tagliatella", "tagliatella",
    "five guys", "rodilla", "pans & company", "pans and company", "pomodoro",
    "la mafia se sienta a la mesa", "udon", "brasa y leña", "ribs", "shibuya", "wok direct",
    "restalia", "papizza", "cantina mariachi", "lizarran", "cañas y tapas",
    
    # Supermercados, hipermercados y alimentación de cadena
    "mercadona", "carrefour", "carrefour express", "carrefour market", "dia", "dia express",
    "supermercados dia", "lidl", "aldi", "alcampo", "hipercor", "el corte ingles",
    "corte ingles", "eroski", "eroski city", "coviran", "masymas", "spar", "primaprix",
    "consum", "bonpreu", "froiz", "a ahorro", "costco", "makro", "supeco", "la sirena",
    
    # Grandes almacenes, moda, calzado y deporte
    "zara", "zara home", "pull&bear", "pull and bear", "massimo dutti", "bershka",
    "stradivarius", "oysho", "mango", "primark", "h&m", "springfield", "cortefiel",
    "women'secret", "womensecret", "pepe jeans", "desigual", "lefties", "kiabi", "c&a",
    "calzedonia", "intimissimi", "tezenis", "bimba y lola", "tous", "pandora",
    "decathlon", "foot locker", "jd sports", "sprinter", "intersport", "aw lab", "snipes",
    "ikea", "leroy merlin", "bricomart", "obramat", "bauhaus", "brico depot",
    "mediamarkt", "fnac", "apple store", "xiaomi store", "samsung store",
    
    # Perfumería, belleza, ópticas y clínicas dentales de cadena
    "druni", "primor", "perfumerias primor", "sephora", "douglas", "marvimundo",
    "bodybell", "rituals", "kiko milano", "yves rocher",
    "alain afflelou", "afflelou", "general optica", "visionlab", "soloptical",
    "multiopticas", "opticalia", "masvision",
    "clinica baviera", "vitaldent", "dentix", "sanitas dental", "adeslas dental",
    "vivanta", "dental company",
    
    # Banca, seguros, telecomunicaciones y paquetería corporate
    "santander", "banco santander", "bbva", "caixabank", "la caixa", "sabadell",
    "banco sabadell", "bankinter", "unicaja", "unicaja banco", "ibercaja", "kutxabank",
    "abanca", "cajasur", "deutsche bank", "ing direct", "ing", "openbank",
    "vodafone", "movistar", "orange", "yoigo", "masmovil", "jazztel", "digi",
    "mapfre", "mutua madrileña", "santa lucia", "allianz", "axa", "zurich", "ocaso",
    "correos", "seur", "mrw", "dhl", "gls", "ups", "fedex",
    
    # Cadenas de gimnasios y hoteles
    "basic-fit", "basic fit", "vivagym", "viva gym", "mcfit", "synergym", "altafit",
    "anytime fitness", "dreamfit", "go fit", "fitness park", "snap fitness",
    "meliá", "melia", "nh hotel", "nh hotels", "barceló", "barcelo", "eurostars",
    "ac hotel", "ac hotels", "ibis", "ibis budget", "marriott", "h10", "radisson",
    "holiday inn", "eurostars hotels",
    
    # Estaciones de servicio y talleres de cadena
    "repsol", "cepsa", "bp", "shell", "galp", "plenoil", "ballenoil", "petroprix",
    "norauto", "midas", "feu vert", "carglass", "euromaster", "first stop", "speedy"
}


def _normalize_text(text: str) -> str:
    """Elimina tildes, signos de puntuación y pasa a minúsculas para comparaciones seguras."""
    if not text:
        return ""
    # Descomponer caracteres acentuados
    nfkd = unicodedata.normalize("NFKD", text)
    clean = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    # Eliminar caracteres no alfanuméricos salvo espacios
    clean = re.sub(r"[^a-z0-9\s]", " ", clean)
    return " ".join(clean.split())


def is_large_business_or_chain(
    name: str,
    types: Optional[List[str]] = None,
    brand: Optional[str] = None,
    primary_type: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Determina si un establecimiento es una gran cadena, franquicia multinacional
    o corporación que deba ser excluida de la prospección comercial de FACENIA.

    Devuelve (es_negocio_grande: bool, motivo: str).
    """
    clean_name = _normalize_text(name)
    clean_brand = _normalize_text(brand or "")

    # 1. Comprobación por tipo de establecimiento
    all_types = [t.lower().strip() for t in (types or [])]
    if primary_type:
        all_types.append(primary_type.lower().strip())

    for t in all_types:
        if t in EXCLUDED_PLACE_TYPES:
            return True, f"Tipo de establecimiento corporativo o gran superficie ({t})"

    # 2. Comprobación por marca o franquicia
    for target in (clean_name, clean_brand):
        if not target:
            continue
        words = set(target.split())
        for chain in KNOWN_LARGE_CHAINS:
            # Coincidencia exacta o coincidencia por palabras completas / prefijo claro
            if chain in target:
                chain_words = chain.split()
                if len(chain_words) == 1:
                    # Palabra individual: verificar que sea una palabra completa en el nombre
                    if chain in words or target.startswith(f"{chain} ") or target.endswith(f" {chain}"):
                        return True, f"Cadena o franquicia identificada ({chain.title()})"
                else:
                    # Frase compuesta (ej. 'burger king', 'carrefour express')
                    return True, f"Cadena o franquicia identificada ({chain.title()})"

    return False, ""
