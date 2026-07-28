"""Справочник лабораторных маркеров: имена, единицы, категории, алиасы.

Содержит ~80 распространённых показателей (в т.ч. ветеринарных) и функции
нормализации имён/единиц, используемые при разборе бланков (ingest.py,
parsers/*) и в веб-интерфейсе.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Нормализация текста
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^0-9a-zа-я]+")


def normalize_marker_name(s: str) -> str:
    """lowercase, ё→е, убрать пробелы/дефисы/точки/скобки и прочую пунктуацию."""
    if s is None:
        return ""
    s = s.strip().lower().replace("ё", "е")
    s = _NON_ALNUM_RE.sub("", s)
    return s


def normalize_unit_str(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip().lower().replace("ё", "е")
    u = u.replace("µ", "мк").replace("μ", "мк")
    u = re.sub(r"\s+", "", u)
    u = u.replace("²", "2")
    for latin, ru in _UNIT_LATIN_TO_RU.items():
        if u == latin:
            return ru
    return u


_UNIT_LATIN_TO_RU = {
    "mmol/l": "ммоль/л",
    "mmol/L".lower(): "ммоль/л",
    "mg/dl": "мг/дл",
    "ng/ml": "нг/мл",
    "pg/ml": "пг/мл",
    "pmol/l": "пмоль/л",
    "nmol/l": "нмоль/л",
    "mcg/l": "мкг/л",
    "mcg/dl": "мкг/дл",
    "ug/l": "мкг/л",
    "ug/dl": "мкг/дл",
    "iu/l": "ед/л",
    "u/l": "ед/л",
    "g/l": "г/л",
    "mm/h": "мм/ч",
    "%": "%",
}


@dataclass
class Analyte:
    code: str
    name_ru: str
    name_en: str
    unit_canonical: str
    category: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# Справочник
# ---------------------------------------------------------------------------
# category: гематология, биохимия, липиды, гормоны щитовидной железы,
# половые гормоны, витамины и микроэлементы, обмен железа, воспаление,
# почки, печень, углеводный обмен, коагулограмма, моча, ветеринарные

ANALYTES: list[Analyte] = [
    # --- Гематология ---------------------------------------------------
    Analyte("hemoglobin", "Гемоглобин", "Hemoglobin", "г/л", "гематология",
            ["hgb", "hb", "гемоглобин", "гемоглобин hb"]),
    Analyte("erythrocytes", "Эритроциты", "RBC", "10^12/л", "гематология",
            ["rbc", "эритроциты", "эритр", "красные кровяные тельца", "количество эритроцитов"]),
    Analyte("hematocrit", "Гематокрит", "Hematocrit", "%", "гематология",
            ["hct", "гематокрит", "ht"]),
    Analyte("mcv", "Средний объём эритроцита (MCV)", "MCV", "фл", "гематология",
            ["mcv", "средний объем эритроцита", "средний объем эритроцитов"]),
    Analyte("mch", "Среднее содержание Hb в эритроците (MCH)", "MCH", "пг", "гематология",
            ["mch", "среднее содержание гемоглобина в эритроците", "среднее содержание hgb в 1 эритроците", "среднее содержание hb в эритроците"]),
    Analyte("mchc", "Средняя концентрация Hb в эритроците (MCHC)", "MCHC", "г/л", "гематология",
            ["mchc", "средняя концентрация гемоглобина в эритроците", "средняя концентрация hb в эритроцитах", "ср конц hb в эр"]),
    Analyte("rdw", "Ширина распределения эритроцитов (RDW)", "RDW", "%", "гематология",
            ["rdw", "ширина распределения эритроцитов по объему"]),
    Analyte("platelets", "Тромбоциты", "Platelets", "10^9/л", "гематология",
            ["plt", "тромбоциты", "количество тромбоцитов"]),
    Analyte("leukocytes", "Лейкоциты", "WBC", "10^9/л", "гематология",
            ["wbc", "лейкоциты", "лейк", "количество лейкоцитов"]),
    Analyte("neutrophils", "Нейтрофилы", "Neutrophils", "%", "гематология",
            ["neut", "нейтрофилы", "нейтрофилы палочкоядерные+сегментоядерные", "нф"]),
    Analyte("lymphocytes", "Лимфоциты", "Lymphocytes", "%", "гематология",
            ["lymph", "lym", "лимфоциты", "лф"]),
    Analyte("monocytes", "Моноциты", "Monocytes", "%", "гематология",
            ["mono", "mon", "моноциты", "мон"]),
    Analyte("eosinophils", "Эозинофилы", "Eosinophils", "%", "гематология",
            ["eo", "eos", "эозинофилы", "эоз"]),
    Analyte("basophils", "Базофилы", "Basophils", "%", "гематология",
            ["baso", "ba", "базофилы", "баз"]),
    # Подвиды нейтрофилов: у них свои нормы, и с общим числом нейтрофилов
    # их смешивать нельзя — палочкоядерных единицы процентов, сегментоядерных
    # больше половины.
    Analyte("neutrophils_band", "Нейтрофилы палочкоядерные", "Band neutrophils", "%", "гематология",
            ["палочкоядерные", "палочкоядерные нейтрофилы", "нейтрофилы палочкоядерные", "band"]),
    Analyte("neutrophils_seg", "Нейтрофилы сегментоядерные", "Segmented neutrophils", "%", "гематология",
            ["сегментоядерные", "сегментоядерные нейтрофилы", "нейтрофилы сегментоядерные", "seg"]),

    Analyte("mpv", "Средний объём тромбоцита (MPV)", "MPV", "фл", "гематология",
            ["mpv", "средний объем тромбоцита", "средний объем тромбоцитов"]),
    Analyte("pct", "Тромбокрит", "PCT", "%", "гематология",
            ["pct", "тромбокрит"]),
    Analyte("myelocytes", "Миелоциты", "Myelocytes", "%", "гематология",
            ["миелоциты"]),
    Analyte("metamyelocytes", "Метамиелоциты (юные)", "Metamyelocytes", "%", "гематология",
            ["метамиелоциты", "метамиелоциты юные", "юные"]),
    Analyte("microcytes", "Микроциты", "Microcytes", "%", "гематология",
            ["микроциты"]),
    Analyte("macrocytes", "Макроциты", "Macrocytes", "%", "гематология",
            ["макроциты"]),
    Analyte("esr", "СОЭ", "ESR", "мм/ч", "гематология",
            ["esr", "соэ", "соэ по вестергрену", "скорость оседания эритроцитов"]),

    # --- Биохимия / углеводный обмен ------------------------------------
    Analyte("total_protein", "Общий белок", "Total protein", "г/л", "биохимия",
            ["tp", "общий белок", "белок общий", "protein total"]),
    Analyte("albumin", "Альбумин", "Albumin", "г/л", "биохимия",
            ["alb", "альбумин"]),
    Analyte("glucose", "Глюкоза", "Glucose", "ммоль/л", "углеводный обмен",
            ["glu", "глюкоза", "глюкоза в крови", "сахар крови"]),
    Analyte("insulin", "Инсулин", "Insulin", "мкЕд/мл", "углеводный обмен",
            ["insulin", "инсулин"]),
    Analyte("homa_ir", "Индекс инсулинорезистентности (HOMA-IR)", "HOMA-IR", "индекс", "углеводный обмен",
            ["homa-ir", "homa ir", "homair", "индекс homa"]),
    Analyte("hba1c", "Гликированный гемоглобин", "HbA1c", "%", "углеводный обмен",
            ["hba1c", "гликированный гемоглобин", "гликозилированный гемоглобин", "hba1с", "гликогемоглобин"]),

    # --- Липиды -----------------------------------------------------------
    Analyte("total_cholesterol", "Общий холестерин", "Total cholesterol", "ммоль/л", "липиды",
            ["холестерин общий", "общий холестерин", "cholesterol total", "chol"]),
    Analyte("ldl", "ЛПНП (холестерин липопротеинов низкой плотности)", "LDL", "ммоль/л", "липиды",
            ["лпнп", "ldl", "холестерин лпнп", "ldl cholesterol"]),
    Analyte("hdl", "ЛПВП (холестерин липопротеинов высокой плотности)", "HDL", "ммоль/л", "липиды",
            ["лпвп", "hdl", "холестерин лпвп", "hdl cholesterol"]),
    Analyte("triglycerides", "Триглицериды", "Triglycerides", "ммоль/л", "липиды",
            ["tg", "триглицериды", "нейтральные жиры"]),

    # --- Печень -------------------------------------------------------
    Analyte("alt", "АЛТ (аланинаминотрансфераза)", "ALT", "Ед/л", "печень",
            ["alt", "алт", "аланинаминотрансфераза", "sgpt"]),
    Analyte("ast", "АСТ (аспартатаминотрансфераза)", "AST", "Ед/л", "печень",
            ["ast", "аст", "аспартатаминотрансфераза", "sgot"]),
    Analyte("ggt", "ГГТ (гамма-глутамилтрансфераза)", "GGT", "Ед/л", "печень",
            ["ggt", "ггт", "гамма-гт", "гамма-глутамилтранспептидаза", "ggtp"]),
    Analyte("alp", "Щелочная фосфатаза", "ALP", "Ед/л", "печень",
            ["alp", "щф", "щелочная фосфатаза"]),
    Analyte("bilirubin_total", "Билирубин общий", "Total bilirubin", "мкмоль/л", "печень",
            ["билирубин общий", "общий билирубин", "bilirubin total", "tbil"]),
    Analyte("bilirubin_direct", "Билирубин прямой", "Direct bilirubin", "мкмоль/л", "печень",
            ["билирубин прямой", "прямой билирубин", "bilirubin direct", "dbil", "билирубин связанный"]),

    # --- Почки -------------------------------------------------------
    Analyte("creatinine", "Креатинин", "Creatinine", "мкмоль/л", "почки",
            ["creatinine", "креатинин", "creat"]),
    Analyte("urea", "Мочевина", "Urea", "ммоль/л", "почки",
            ["urea", "мочевина", "bun"]),
    Analyte("uric_acid", "Мочевая кислота", "Uric acid", "мкмоль/л", "почки",
            ["мочевая кислота", "uric acid", "ua"]),
    Analyte("gfr", "СКФ (скорость клубочковой фильтрации)", "GFR", "мл/мин/1.73м2", "почки",
            ["скф", "gfr", "скорость клубочковой фильтрации", "egfr"]),

    # --- Воспаление -----------------------------------------------------
    Analyte("crp", "С-реактивный белок", "CRP", "мг/л", "воспаление",
            ["crp", "срб", "с-реактивный белок", "c-реактивный белок", "срб32", "среактивныйбелоксрб32", "hs-crp", "с реактивный белок"]),
    Analyte("homocysteine", "Гомоцистеин", "Homocysteine", "мкмоль/л", "воспаление",
            ["гомоцистеин", "homocysteine", "hcy"]),

    # --- Обмен железа -----------------------------------------------------
    Analyte("ferritin", "Ферритин", "Ferritin", "нг/мл", "обмен железа",
            ["ферритин", "ferritin", "ferr"]),
    Analyte("serum_iron", "Железо сыворотки", "Serum iron", "мкмоль/л", "обмен железа",
            ["железо", "железо сыворотки", "iron", "fe сыворотки", "сывороточное железо"]),
    Analyte("tibc", "ОЖСС (общая железосвязывающая способность)", "TIBC", "мкмоль/л", "обмен железа",
            ["ожсс", "tibc", "общая железосвязывающая способность"]),
    Analyte("transferrin", "Трансферрин", "Transferrin", "г/л", "обмен железа",
            ["трансферрин", "transferrin", "tf"]),
    Analyte("transferrin_saturation", "Насыщение трансферрина железом", "Transferrin saturation", "%", "обмен железа",
            ["насыщение трансферрина", "процент насыщения трансферрина железом", "tsat", "sat trf"]),

    # --- Витамины и микроэлементы ------------------------------------------
    Analyte("vitamin_b12", "Витамин B12 (цианокобаламин)", "Vitamin B12", "пг/мл", "витамины и микроэлементы",
            ["витамин b12", "vitamin b12", "b12", "цианокобаламин", "витамин в12"]),
    Analyte("folic_acid", "Фолиевая кислота (витамин B9)", "Folic acid", "нг/мл", "витамины и микроэлементы",
            ["фолиевая кислота", "folic acid", "фолаты", "витамин b9", "витамин в9", "folate"]),
    Analyte("vitamin_d", "Витамин D (25-OH)", "Vitamin D (25-OH)", "нг/мл", "витамины и микроэлементы",
            ["витамин d", "25-oh витамин d", "25(oh)d", "vitamin d 25-oh", "витамин д", "25-oh-d3", "кальцидиол"]),
    Analyte("magnesium", "Магний", "Magnesium", "ммоль/л", "витамины и микроэлементы",
            ["магний", "magnesium", "mg"]),
    Analyte("calcium_total", "Кальций общий", "Calcium total", "ммоль/л", "витамины и микроэлементы",
            ["кальций общий", "кальций", "calcium total", "ca общий"]),
    Analyte("calcium_ionized", "Кальций ионизированный", "Calcium ionized", "ммоль/л", "витамины и микроэлементы",
            ["кальций ионизированный", "ионизированный кальций", "ca ionized", "ca2+"]),
    Analyte("phosphorus", "Фосфор", "Phosphorus", "ммоль/л", "витамины и микроэлементы",
            ["фосфор", "phosphorus", "неорганический фосфор", "p"]),
    Analyte("potassium", "Калий", "Potassium", "ммоль/л", "витамины и микроэлементы",
            ["калий", "potassium", "k+", "k"]),
    Analyte("sodium", "Натрий", "Sodium", "ммоль/л", "витамины и микроэлементы",
            ["натрий", "sodium", "na+", "na"]),
    Analyte("chloride", "Хлор", "Chloride", "ммоль/л", "витамины и микроэлементы",
            ["хлор", "хлориды", "chloride", "cl-", "cl"]),
    Analyte("zinc", "Цинк", "Zinc", "мкмоль/л", "витамины и микроэлементы",
            ["цинк", "zinc", "zn"]),

    # --- Гормоны щитовидной железы -----------------------------------------
    Analyte("tsh", "ТТГ (тиреотропный гормон)", "TSH", "мЕд/л", "гормоны щитовидной железы",
            ["ттг", "tsh", "тиреотропный гормон"]),
    Analyte("free_t4", "Т4 свободный", "Free T4", "пмоль/л", "гормоны щитовидной железы",
            ["т4 свободный", "свободный т4", "free t4", "ft4"]),
    Analyte("free_t3", "Т3 свободный", "Free T3", "пмоль/л", "гормоны щитовидной железы",
            ["т3 свободный", "свободный т3", "free t3", "ft3"]),
    Analyte("anti_tpo", "АТ-ТПО (антитела к тиреопероксидазе)", "Anti-TPO", "Ед/мл", "гормоны щитовидной железы",
            ["ат-тпо", "ат к тпо", "антитела к тиреопероксидазе", "anti-tpo", "atpo"]),
    Analyte("anti_tg", "АТ-ТГ (антитела к тиреоглобулину)", "Anti-TG", "Ед/мл", "гормоны щитовидной железы",
            ["ат-тг", "ат к тг", "антитела к тиреоглобулину", "anti-tg", "atg"]),

    # --- Половые гормоны -----------------------------------------------------
    Analyte("prolactin", "Пролактин", "Prolactin", "мЕд/л", "половые гормоны",
            ["пролактин", "prolactin", "prl"]),
    Analyte("cortisol", "Кортизол", "Cortisol", "нмоль/л", "половые гормоны",
            ["кортизол", "cortisol"]),
    Analyte("estradiol", "Эстрадиол", "Estradiol", "пг/мл", "половые гормоны",
            ["эстрадиол", "estradiol", "e2"]),
    Analyte("progesterone", "Прогестерон", "Progesterone", "нмоль/л", "половые гормоны",
            ["прогестерон", "progesterone"]),
    Analyte("testosterone", "Тестостерон", "Testosterone", "нмоль/л", "половые гормоны",
            ["тестостерон", "testosterone", "тестостерон общий"]),
    Analyte("fsh", "ФСГ (фолликулостимулирующий гормон)", "FSH", "мЕд/мл", "половые гормоны",
            ["фсг", "fsh", "фолликулостимулирующий гормон"]),
    Analyte("lh", "ЛГ (лютеинизирующий гормон)", "LH", "мЕд/мл", "половые гормоны",
            ["лг", "lh", "лютеинизирующий гормон"]),
    Analyte("amh", "АМГ (антимюллеров гормон)", "AMH", "нг/мл", "половые гормоны",
            ["амг", "amh", "антимюллеров гормон"]),
    Analyte("dhea_s", "ДГЭА-С (дегидроэпиандростерон-сульфат)", "DHEA-S", "мкмоль/л", "половые гормоны",
            ["дгэа-с", "дгэа-сульфат", "dhea-s", "dheas"]),

    # --- Коагулограмма -----------------------------------------------------
    Analyte("fibrinogen", "Фибриноген", "Fibrinogen", "г/л", "коагулограмма",
            ["фибриноген", "fibrinogen", "fib"]),
    Analyte("inr", "МНО (международное нормализованное отношение)", "INR", "индекс", "коагулограмма",
            ["мно", "inr", "международное нормализованное отношение"]),
    Analyte("aptt", "АЧТВ (активированное частичное тромбопластиновое время)", "APTT", "сек", "коагулограмма",
            ["ачтв", "aptt", "aptt/ачтв", "ptt"]),
    Analyte("d_dimer", "D-димер", "D-dimer", "мкг/мл", "коагулограмма",
            ["d-димер", "d-dimer", "ддимер", "d dimer"]),

    # --- Моча -----------------------------------------------------
    Analyte("urine_protein", "Белок в моче", "Urine protein", "г/л", "моча",
            ["белок в моче", "белок мочи", "urine protein", "протеинурия"]),
    Analyte("urine_glucose", "Глюкоза в моче", "Urine glucose", "ммоль/л", "моча",
            ["глюкоза в моче", "сахар в моче", "urine glucose", "глюкозурия"]),
    Analyte("urine_specific_gravity", "Удельный вес мочи", "Urine specific gravity", "", "моча",
            ["удельный вес", "относительная плотность мочи", "specific gravity", "sg"]),
    Analyte("urine_leukocytes", "Лейкоциты в моче", "Urine leukocytes", "в п/зр", "моча",
            ["лейкоциты в моче", "лейкоциты мочи", "urine leukocytes", "лейк. в п/зр"]),
    Analyte("urine_ph", "pH мочи", "Urine pH", "", "моча",
            ["ph мочи", "реакция мочи", "urine ph", "ph"]),

    # --- Ветеринарные (маркеры, специфичные для собак) --------------------
    Analyte("vet_amylase", "Амилаза", "Amylase", "Ед/л", "ветеринарные",
            ["амилаза", "amylase", "amyl"]),
    Analyte("vet_lipase", "Липаза", "Lipase", "Ед/л", "ветеринарные",
            ["липаза", "lipase", "lip"]),
    Analyte("vet_total_t4", "Т4 общий (вет.)", "Total T4", "нмоль/л", "ветеринарные",
            ["т4 общий", "общий т4", "total t4", "tt4"]),
    Analyte("vet_fructosamine", "Фруктозамин", "Fructosamine", "мкмоль/л", "ветеринарные",
            ["фруктозамин", "fructosamine", "fruct"]),
]

# Латинские алиасы для «человеческих» маркеров, которые массово встречаются
# и в ветеринарных бланках (ALT, AST, ALP, креатинин, мочевина, общий белок,
# альбумин, глюкоза) — сами показатели одни и те же, добавляем алиасы прямо
# к существующим записям, чтобы не плодить дублирующиеся коды.
_VET_SHARED_ALIASES = {
    "alt": ["alt (вет)"],
    "ast": ["ast (вет)"],
    "alp": ["alp (вет)", "alkp"],
    "creatinine": ["creat (вет)"],
    "urea": ["urea (вет)", "bun (вет)"],
    "total_protein": ["tp (вет)"],
    "albumin": ["alb (вет)"],
    "glucose": ["glu (вет)"],
}
for _code, _extra in _VET_SHARED_ALIASES.items():
    for _a in _extra:
        for _an in ANALYTES:
            if _an.code == _code:
                _an.aliases.append(_a)

ANALYTES_BY_CODE: dict[str, Analyte] = {a.code: a for a in ANALYTES}

# нормализованный алиас -> код аналита
ALIAS_INDEX: dict[str, str] = {}
for _an in ANALYTES:
    all_names = [_an.code, _an.name_ru, _an.name_en, *_an.aliases]
    for _name in all_names:
        if not _name:
            continue
        norm = normalize_marker_name(_name)
        if norm and norm not in ALIAS_INDEX:
            ALIAS_INDEX[norm] = _an.code


def get_analyte(code: str) -> Analyte | None:
    return ANALYTES_BY_CODE.get(code)


_PAREN_RE = re.compile(r"\(([^)]*)\)")
# Коды номенклатуры медуслуг и ссылки на приказы, которыми лаборатории
# засоряют название: «Глюкоза A09.05.023 (Приказ МЗ РФ № 804н)».
_MED_CODE_RE = re.compile(r"\b[A-ZА-Я]\d{2}(?:\.\d{2,3}){1,3}\b|приказ[^)]*", re.I)


def _name_variants(raw_name: str) -> list[str]:
    """Варианты написания названия, от самого полного к самому короткому.

    Лаборатории пишут «Аланинаминотрансфераза (АЛТ)», «АЛТ (ALT)»,
    «Средний объём эритроцитов (MCV)» — полезная часть то снаружи скобок,
    то внутри. Поэтому пробуем и то, и другое, а служебные коды выкидываем.
    """
    if not raw_name:
        return []
    cleaned = _MED_CODE_RE.sub(" ", raw_name)
    inside = [g.strip() for g in _PAREN_RE.findall(cleaned) if g.strip()]
    outside = _PAREN_RE.sub(" ", cleaned).strip()
    variants = [cleaned, outside, *inside]
    seen, out = set(), []
    for v in variants:
        n = normalize_marker_name(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def match_analyte(raw_name: str) -> str | None:
    """Сопоставить произвольное имя маркера из бланка с кодом аналита."""
    norm = normalize_marker_name(raw_name)
    if not norm:
        return None
    # точное совпадение по любому из вариантов написания
    for variant in _name_variants(raw_name):
        if variant in ALIAS_INDEX:
            return ALIAS_INDEX[variant]
    # Частичное совпадение — только если алиас покрывает БОЛЬШУЮ часть имени.
    # Без этого условия короткий алиас проглатывает длинные составные названия:
    # «Средний объём эритроцитов (MCV)» цеплялся за алиас «эритроциты» и
    # ложился в один график с самим количеством эритроцитов, хотя это разные
    # показатели с разными единицами (фл против x10*12/л).
    MIN_COVERAGE = 0.6
    best = None
    best_len = 0
    for alias, code in ALIAS_INDEX.items():
        if len(alias) < 4:
            continue
        if alias in norm or norm in alias:
            # Перекрытие считается как отношение короткой строки к длинной.
            # Раньше в числителе была длина алиаса, и длинный алиас
            # «нейтрофилыпалочкоядерныесегментоядерные» давал перекрытие 1.0
            # для короткого «палочкоядерные» — подвид нейтрофилов попадал в
            # один ряд с их общим числом, и график прыгал от 1 % до 85 %.
            coverage = min(len(alias), len(norm)) / max(len(alias), len(norm))
            if coverage < MIN_COVERAGE:
                continue
            if len(alias) > best_len:
                best = code
                best_len = len(alias)
    return best


def categories() -> list[str]:
    seen = []
    for a in ANALYTES:
        if a.category not in seen:
            seen.append(a.category)
    return seen


# ---------------------------------------------------------------------------
# Конверсия единиц
# ---------------------------------------------------------------------------
# CONVERSIONS[code][нормализованная_альтернативная_единица] = множитель,
# на который умножается значение в альтернативной единице, чтобы получить
# значение в канонической единице (unit_canonical).

CONVERSIONS: dict[str, dict[str, float]] = {
    "glucose": {"мг/дл": 1 / 18.0182},
    "total_cholesterol": {"мг/дл": 1 / 38.67},
    "ldl": {"мг/дл": 1 / 38.67},
    "hdl": {"мг/дл": 1 / 38.67},
    "triglycerides": {"мг/дл": 1 / 88.57},
    "vitamin_d": {"нмоль/л": 1 / 2.496},
    "vitamin_b12": {"пмоль/л": 1.355},
    "ferritin": {"мкг/л": 1.0},
    "serum_iron": {"мкг/дл": 1 / 5.585},
    "tibc": {"мкг/дл": 1 / 5.585},
    "testosterone": {"нг/мл": 3.467},
    "estradiol": {"пмоль/л": 1 / 3.671},
    "progesterone": {"нг/мл": 3.18},
    "cortisol": {"мкг/дл": 27.59},
    "creatinine": {"мг/дл": 88.4},
    "urea": {"мг/дл": 1 / 6.006},
    "uric_acid": {"мг/дл": 59.48},
    "calcium_total": {"мг/дл": 1 / 4.008},
    "calcium_ionized": {"мг/дл": 1 / 4.008},
    "magnesium": {"мг/дл": 1 / 2.431},
    "phosphorus": {"мг/дл": 1 / 3.097},
}


def sync_to_db(conn) -> None:
    """Синхронизировать справочник ANALYTES в таблицы analytes/analyte_aliases.

    Идемпотентно: существующие записи (по code) обновляются, id сохраняется
    (на него ссылаются results.analyte_id), алиасы каждый раз перезаписываются
    текущим списком.
    """
    for a in ANALYTES:
        row = conn.execute("SELECT id FROM analytes WHERE code=?", (a.code,)).fetchone()
        if row:
            analyte_id = row["id"]
            conn.execute(
                "UPDATE analytes SET name_ru=?, name_en=?, unit_canonical=?, category=?, description=? WHERE id=?",
                (a.name_ru, a.name_en, a.unit_canonical, a.category, a.description, analyte_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO analytes(code, name_ru, name_en, unit_canonical, category, description) "
                "VALUES (?,?,?,?,?,?)",
                (a.code, a.name_ru, a.name_en, a.unit_canonical, a.category, a.description),
            )
            analyte_id = cur.lastrowid

        conn.execute("DELETE FROM analyte_aliases WHERE analyte_id=?", (analyte_id,))
        seen: set[str] = set()
        for alias in [a.code, a.name_ru, a.name_en, *a.aliases]:
            if not alias:
                continue
            norm = normalize_marker_name(alias)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            conn.execute(
                "INSERT INTO analyte_aliases(analyte_id, alias) VALUES (?,?)",
                (analyte_id, norm),
            )


def normalize_unit(value: float | None, unit: str | None, analyte_code: str | None):
    """Привести значение к канонической единице аналита, если известна конверсия.

    Возвращает (value, unit). Если конверсия неизвестна или значение пустое —
    возвращает вход без изменений (кроме косметической нормализации записи
    единицы, если она уже соответствует канонической).
    """
    if value is None or analyte_code is None:
        return value, unit
    analyte = ANALYTES_BY_CODE.get(analyte_code)
    if analyte is None:
        return value, unit
    norm_unit = normalize_unit_str(unit)
    norm_canonical = normalize_unit_str(analyte.unit_canonical)
    if not norm_unit or norm_unit == norm_canonical:
        return value, (analyte.unit_canonical if norm_unit == norm_canonical else unit)
    table = CONVERSIONS.get(analyte_code, {})
    if norm_unit in table:
        factor = table[norm_unit]
        converted = round(value * factor, 4)
        return converted, analyte.unit_canonical
    return value, unit


# ---------------------------------------------------------------------------
# Приведение единиц к одной шкале
# ---------------------------------------------------------------------------

# Одна и та же величина записывается лабораториями по-разному. Пока записи
# лежат в разных единицах, график рвётся: гемоглобин 11,9 г/дл и 119 г/л —
# одно значение, но выглядит как обвал в десять раз.
#
# Коэффициент 1.0 означает синоним (иная запись той же единицы), остальные —
# настоящий пересчёт. Референсы пересчитываются тем же коэффициентом:
# сравнивать значение с непересчитанной границей нельзя.

UNIT_SYNONYMS: dict[str, str] = {
    # концентрация клеток
    "тыс/мкл": "10^9/л", "x10*9/л": "10^9/л", "10*9/литр": "10^9/л",
    "10^9/l": "10^9/л", "10*9/л": "10^9/л", "10e9/л": "10^9/л",
    "млн/мкл": "10^12/л", "x10*12/л": "10^12/л", "10*12/литр": "10^12/л",
    "10^12/l": "10^12/л", "10*12/л": "10^12/л",
    # тиреотропный гормон: все четыре записи — одно и то же
    "мкме/мл": "мЕд/л", "ммe/л": "мЕд/л", "мме/л": "мЕд/л",
    "мкмед/мл": "мЕд/л", "мед/л": "мЕд/л", "мкед/мл": "мЕд/л",
    "miu/l": "мЕд/л", "uiu/ml": "мЕд/л",
}

# (код аналита, единица из бланка) → (множитель, каноническая единица)
UNIT_CONVERSIONS: dict[tuple[str, str], tuple[float, str]] = {
    ("hemoglobin", "г/дл"): (10.0, "г/л"),
    ("hemoglobin", "g/dl"): (10.0, "г/л"),
    ("mchc", "г/дл"): (10.0, "г/л"),
    ("free_t4", "нг/дл"): (12.87, "пмоль/л"),
    ("free_t3", "пг/мл"): (1.536, "пмоль/л"),
    # Минералы в массовых единицах: делим на молярную массу элемента.
    # Проверено на бланке — 102,06 мг/л кальция дают 2,55 ммоль/л, ровно
    # верхнюю границу нормы, которая в том же бланке и напечатана.
    ("calcium_total", "мг/л"): (1 / 40.08, "ммоль/л"),
    ("magnesium", "мг/л"): (1 / 24.31, "ммоль/л"),
    ("potassium", "мг/л"): (1 / 39.10, "ммоль/л"),
    ("sodium", "мг/л"): (1 / 22.99, "ммоль/л"),
}


def canonical_unit(unit: str | None) -> str | None:
    """Свести разные записи одной единицы к одной форме."""
    if not unit:
        return unit
    key = unit.strip().lower().replace(" ", "").replace("ё", "е")
    return UNIT_SYNONYMS.get(key, unit.strip())


def convert_to_canonical(code: str | None, value, unit: str | None,
                         ref_low=None, ref_high=None):
    """Привести значение и референс к канонической единице маркера.

    Возвращает (value, unit, ref_low, ref_high). Если пересчёт неизвестен,
    значение остаётся как есть — выдумывать коэффициент нельзя.
    """
    unit = canonical_unit(unit)
    if not code or not unit:
        return value, unit, ref_low, ref_high
    factor_unit = UNIT_CONVERSIONS.get((code, unit.strip().lower()))
    if not factor_unit:
        return value, unit, ref_low, ref_high
    factor, target = factor_unit
    scale = lambda v: (round(v * factor, 4) if v is not None else None)
    return scale(value), target, scale(ref_low), scale(ref_high)


# ---------------------------------------------------------------------------
# Проверка правдоподобия
# ---------------------------------------------------------------------------

# Физиологически возможные границы — не норма, а пределы, за которыми
# значение почти наверняка означает ошибку разбора или сопоставления:
# либо парсер взял не то число, либо название легло не на тот маркер.
# «Гликогемоглобин 4,6 %» попал в гемоглобин именно так — 4,6 г/л крови
# не бывает ни у кого живого.
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "hemoglobin": (20, 250),          # г/л
    "erythrocytes": (1.0, 9.0),       # 10^12/л
    "leukocytes": (0.1, 100),         # 10^9/л
    "platelets": (1, 2000),           # 10^9/л
    "hematocrit": (10, 70),           # %
    "glucose": (0.5, 40),             # ммоль/л
    "total_protein": (20, 120),       # г/л
    "albumin": (10, 70),              # г/л
    "creatinine": (10, 1500),         # мкмоль/л
    "urea": (0.3, 60),                # ммоль/л
    "total_cholesterol": (0.5, 25),   # ммоль/л
    "hba1c": (2, 20),                 # %
    "tsh": (0.001, 200),              # мЕд/л
    "esr": (0, 150),                  # мм/ч
    "ferritin": (0.1, 5000),          # нг/мл
    "serum_iron": (0.5, 100),         # мкмоль/л
}


def implausible(code: str | None, value, unit: str | None = None) -> bool:
    """Похоже ли значение на ошибку разбора, а не на измерение.

    Проверка нарочно грубая: границы взяты заведомо шире любых клинических,
    чтобы не отбраковать настоящий тяжёлый результат. Срабатывание значит
    «скорее всего, число или маркер определены неверно», а не «пациент болен».
    """
    if value is None or not code:
        return False
    rng = PLAUSIBLE_RANGE.get(code)
    if not rng:
        return False
    return not (rng[0] <= value <= rng[1])
