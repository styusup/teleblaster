import csv
import json
import random
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse


MEMBER_HEADERS = ["Name", "ID", "Username", "Access Hash", "Gender", "Group Name", "Group ID"]


# Dataset nama depan Indonesia (lowercase) untuk infer gender. Hanya nama yang
# sangat dominan satu jenis kelamin (>90% di data publik) yang dimasukkan ke
# kamus eksplisit; selainnya dilempar ke heuristic suffix.
_INDO_MALE_NAMES = frozenset({
    "abdul", "abdullah", "aditya", "adit", "adi", "afif", "agus", "agung",
    "ahmad", "ahmed", "ahsan", "akbar", "ali", "alif", "amin", "andi",
    "andika", "andre", "andri", "anggara", "anto", "ardi", "ardian",
    "arie", "arif", "arifin", "aris", "ariyanto", "arya", "asep",
    "bagas", "bagus", "bambang", "bayu", "benny", "bima", "bobby", "budi",
    "cahyo", "candra", "chandra", "cipto", "darto", "dani", "danu", "darwin",
    "david", "dedi", "dedy", "deni", "denny", "dharma", "dhani", "dicky",
    "didik", "dimas", "dion", "dodi", "dody", "donny", "doni", "dony",
    "edi", "edo", "edy", "eko", "eric", "ery", "fadli", "fahmi",
    "fahri", "faisal", "faiz", "fajar", "farhan", "farid", "fauzan", "fauzi",
    "ferdi", "fery", "ferry", "ganang", "ganda", "guntur", "gusti", "hadi",
    "hadyan", "haikal", "halim", "hamdan", "hamid", "hanif", "harry", "haris",
    "haryanto", "hasan", "hendra", "hendri", "hendry", "heri", "hermawan",
    "hidayat", "ibnu", "ibrahim", "ichsan", "iqbal", "ikhsan", "ilham", "imam",
    "imron", "indra", "ipung", "irfan", "irwan", "iskandar", "iwan", "jaka",
    "jefri", "joko", "jusuf", "khairul", "khoirul", "krisna", "kurniawan",
    "kus", "kusuma", "lukman", "maulana", "michael", "miftahul",
    "mochamad", "mochammad", "mohamad", "mohammad", "mohamed", "muhammad",
    "muhamad", "muklas", "mukti", "mulyadi", "munir", "musa", "naufal", "nico",
    "noval", "nurdin", "oki", "panji", "prabowo", "pramudya",
    "pranoto", "pras", "pratama", "priyo", "purnomo", "putra", "raditya",
    "rafi", "raihan", "rama", "ramadhan", "randy", "rasyid", "raymond",
    "razak", "reno", "ricky", "ridho", "ridwan", "rifqi", "riki",
    "rio", "riski", "riyadi", "rizal", "rizky", "robert", "robi", "rohman",
    "roni", "ronny", "rudi", "rudy", "ryan", "sahrul", "saiful", "sandi",
    "sandy", "santoso", "saputra", "satria", "satrio", "septian",
    "setiawan", "setiyo", "sigit", "slamet", "soni", "sony", "subhan",
    "sudirman", "suharto", "sukarno", "sulaiman", "sumanto", "supardi",
    "suparman", "supri", "supriyadi", "suryadi", "susanto", "syaiful",
    "syamsul", "syarif", "tafsir", "taufik", "teddy", "tegar", "tio",
    "tomi", "tommy", "toni", "tony", "topan", "trianto", "udin",
    "ujang", "umar", "vino", "wahyu", "wawan", "wibowo", "widodo", "willy",
    "winardi", "winarno", "wisnu", "yanto", "yoga", "yogi", "yopi", "yudi",
    "yudo", "yulianto", "yunus", "yusuf", "zaki", "zainal", "zainuddin",
    "zaenal", "zulfikar",
})

_INDO_FEMALE_NAMES = frozenset({
    "ade", "adel", "adelia", "adinda", "agnes", "ainun", "alya", "amalia",
    "amanda", "ami", "amelia", "ana", "anastasia", "anggi", "anggraeni",
    "anggraini", "ani", "anik", "anita", "anna", "annisa", "april",
    "aprilia", "arini", "asih", "astri", "astrid", "astuti", "aulia",
    "ayu", "ayunda", "bella", "bunga", "carla", "cici", "cindy", "cinta",
    "citra", "clarissa", "cynthia", "dahlia", "debby", "debora", "dela",
    "delia", "desi", "dessy", "destiana", "destiani", "devi", "devina",
    "dewi", "diah", "diana", "dian", "dilla", "dina", "dini",
    "dita", "ditha", "dwina", "ekaputri", "elena", "elisabet",
    "elizabeth", "ella", "elsa", "elvina", "emi", "emy", "endah", "ening",
    "erika", "erna", "esa", "esther", "evelyn", "evi", "fani", "fanni",
    "farah", "farida", "fatma", "fatimah", "febi", "febriana", "febriani",
    "feby", "fenny", "fika", "fina", "fira", "fitri", "fitria", "fitriani",
    "frida", "gabriela", "gita", "grace", "hana", "hanna", "hartini",
    "hasanah", "helena", "henny", "herlina", "hesti", "ifa", "ika", "iin",
    "ima", "imas", "ina", "indah", "indriani", "ines", "intan", "ira",
    "iren", "irene", "isma", "isnaeni", "ismi", "ita", "ivana", "jasmine",
    "jenny", "jihan", "joice", "julia", "juliana", "kartika", "kartini",
    "kasih", "khairunnisa", "khairunisa", "kiki", "kirana", "kristina",
    "lala", "laras", "larasati", "laura", "lely", "leny", "lestari",
    "leticia", "lia", "lidia", "lilik", "lilis", "lina", "linda", "lisa",
    "lola", "lulu", "luna", "maharani", "maria", "mariana",
    "marlina", "marni", "martha", "marwah", "maya", "mega", "meilani",
    "melati", "melinda", "mellisa", "melissa", "melly", "merry", "mia",
    "mira", "mirna", "monica", "murni", "mutia", "nadia", "nadine", "nana",
    "nanda", "natalia", "natasha", "nelly", "neneng", "nia", "nida", "nike",
    "nila", "ningsih", "nirmala", "nita", "noni", "nora", "novi", "noviana",
    "novita", "nufus", "nur", "nurul", "olivia", "ovi", "patricia", "permata",
    "pipit", "prita", "puji", "purnama", "puspa", "puspita", "putri", "rachel",
    "rachma", "rahayu", "rahma", "rahmawati", "rara", "rani", "rasti", "ratih",
    "ratna", "ratu", "renata", "resti", "retno", "ria", "riana",
    "rida", "rika", "rina", "rini", "risa", "risma", "rissa", "rita", "rizka",
    "rofiqoh", "romi", "rosa", "rosalia", "rosita", "rossa", "rukmini",
    "sabrina", "safira", "saidah", "salma", "salsa", "salsabila", "samsiah",
    "sandra", "santi", "santika", "santy", "sari", "sarah", "sasha", "selena",
    "sella", "septiana", "sevi", "sherly", "shinta", "shira", "sila", "silvi",
    "silvia", "sinta", "siska", "siti", "sofia", "sofyana", "sonia",
    "stefani", "stella", "suci", "sugiarti", "sulis", "sulistya", "sumarni",
    "sumiyati", "sundari", "sunarti", "supiani", "surya", "susan", "susanti",
    "susi", "syahrini", "syifa", "tabitha", "tania", "tanti", "tari",
    "tasya", "tatiana", "tia", "tika", "tina", "tiwi", "tuti",
    "ulfa", "uli", "uma", "umi", "umy", "uut", "vania", "vena", "vera",
    "veronica", "vidya", "vina", "vioni", "vita", "wanda",
    "wati", "weni", "widya", "wina", "winda", "winny", "wita", "wiwi",
    "wulan", "yana", "yanti", "yatmi", "yayu", "yeni", "yessi", "yolanda",
    "yuli", "yuliana", "yuliani", "yulianti", "yunita", "yuni", "zahra",
    "zara", "zaskia", "zia", "zulaikha",
})

_FEMALE_SUFFIXES = (
    "wati", "sari", "ningsih", "lestari", "ningrum", "anti", "awati",
    "iyah", "iah", "siah", "yati", "yanti", "tari", "santi", "winda",
    "wiyah",
)

_MALE_SUFFIXES = (
    "anto", "awan", "iyanto", "iadi", "iono", "iudin", "rudin", "huddin",
    "uddin", "rian", "fian",
)


def infer_gender(name: str) -> str:
    """Best-effort tebak jenis kelamin dari nama.

    Return:
        "L" untuk laki-laki, "P" untuk perempuan, "?" jika tidak yakin.

    Strategi:
        1. Cek seluruh token nama (kasus banyak orang Indonesia pakai >1 nama).
        2. Cek kamus eksplisit nama Indonesia umum.
        3. Fall back ke heuristic akhiran (suffix) yang sangat dominan.
        4. Jika ambigu (token cocok di kedua kelas), kembalikan "?".

    Pengembalian "?" tidak ditulis ke CSV; field Gender disisakan kosong agar
    workflow yang membaca CSV tidak salah interpretasi.
    """
    if not name:
        return "?"
    tokens = re.findall(r"[A-Za-z\u00C0-\u017F']+", name.lower())
    if not tokens:
        return "?"

    male_hits = 0
    female_hits = 0

    for tok in tokens:
        if tok in _INDO_MALE_NAMES:
            male_hits += 2  # kamus lebih kuat dari suffix
        if tok in _INDO_FEMALE_NAMES:
            female_hits += 2
        # Suffix check (lebih lemah, hanya untuk first token agar tidak salah
        # baca akhiran nama belakang).
    first = tokens[0]
    if male_hits == 0 and female_hits == 0:
        if any(first.endswith(sfx) for sfx in _FEMALE_SUFFIXES):
            female_hits += 1
        elif any(first.endswith(sfx) for sfx in _MALE_SUFFIXES):
            male_hits += 1

    if male_hits > female_hits:
        return "L"
    if female_hits > male_hits:
        return "P"
    return "?"

SCRAPE_RESULTS_DIR = "Hasil Scrape Member"

_FS_INVALID_CHARS = set('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_filename(name: str, max_len: int = 100, fallback: str = "untitled") -> str:
    """Return a filesystem-safe version of `name` suitable for cross-platform filenames.

    Replaces characters illegal on Windows (`<>:"/\\|?*`) and control chars (ord < 32)
    with underscores, trims surrounding whitespace and trailing dots, avoids reserved
    Windows device names (CON, PRN, COM1, etc.), and truncates to `max_len` chars.
    Returns `fallback` if the result would otherwise be empty.
    """
    cleaned = "".join(
        "_" if (c in _FS_INVALID_CHARS or ord(c) < 32) else c
        for c in (name or "")
    ).strip().rstrip(". ").strip()

    if cleaned.upper().split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned

    if not cleaned:
        return fallback

    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ").strip() or fallback

    return cleaned


def per_group_members_path(members_csv_path: str, group_title: str) -> Path:
    """Build the path for a per-group scrape CSV next to `members_csv_path`.

    Result: `<members_csv parent>/Hasil Scrape Member/<sanitized title>.csv`.
    """
    csv_path = Path(members_csv_path)
    base_dir = csv_path.parent if str(csv_path.parent) not in ("", ".") else Path(".")
    safe = sanitize_filename(group_title)
    return base_dir / SCRAPE_RESULTS_DIR / f"{safe}.csv"


def ensure_paths(*paths: str) -> None:
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return "***"
    return f"{phone[:5]}***{phone[-3:]}"


def is_valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"\+?[1-9]\d{6,14}", phone.strip()))


def load_json(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path: str, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(p.parent)) as tf:
        json.dump(data, tf, indent=2, ensure_ascii=True)
        temp_name = tf.name
    Path(temp_name).replace(p)


def read_members_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_members_csv_atomic(path: str, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="", dir=str(p.parent)) as tf:
        writer = csv.DictWriter(tf, fieldnames=MEMBER_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        temp_name = tf.name
    Path(temp_name).replace(p)


def append_members_dedup(path: str, rows: list[dict]) -> tuple[int, int]:
    existing = read_members_csv(path)
    seen = {row.get("ID", "") for row in existing}
    before = len(existing)
    for row in rows:
        rid = str(row.get("ID", ""))
        if rid and rid not in seen:
            existing.append(row)
            seen.add(rid)
    write_members_csv_atomic(path, existing)
    return before, len(existing)


def random_delay(low: int, high: int) -> float:
    return random.uniform(low, high)


def normalize_menu_choice(raw: str) -> str:
    value = (raw or "").strip()
    if value.isdigit() and len(value) < 2:
        return value.zfill(2)
    return value


def normalize_chat_target(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""

    # Preserve raw numeric chat IDs such as -100xxxxxxxxxx.
    if re.fullmatch(r"-?\d+", value):
        return value

    if value.startswith("@"):
        return value[1:]

    if value.startswith("t.me/"):
        value = "https://" + value

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        host = (parsed.netloc or "").lower()
        if host.endswith("t.me") or host.endswith("telegram.me"):
            path = (parsed.path or "").lstrip("/")
            if not path:
                return ""
            if path.startswith("joinchat/"):
                path = path.split("/", 1)[1]
                return "+" + path if path else ""
            return path.split("/", 1)[0]

    return value
