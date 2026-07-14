"""傷寒學派註冊表（SchoolID）：解釋範式的結構化建模。

學派歸屬本身是學術史層面的「後世歸納」（source_level=posthoc_induction），
依據通行傷寒學術史分類（任應秋《中醫各家學說》、《傷寒論》流派研究的
標準框架）人工整編，只收語料中實際在庫的著者；每個學派記錄解釋範式、
代表著作、適用邊界與對立學派。

與純編輯性名錄不同：註冊表在構建時把注家分歧圖譜（C 層可計算資產）的
一致度矩陣回填進來——同派/跨派注家對的實測一致度，讓「學派分野」
成為可由數據檢驗的命題而非單純標籤（如錯簡重訂 × 維護舊論的跨派對
一致度顯著低於派內對）。
"""
from __future__ import annotations

import json
from typing import Dict, List

from .. import config

# ---------------------------------------------------------------------------
# 學派定義（成員僅列語料在庫著者；book_dirs 為其在庫著作）
# ---------------------------------------------------------------------------
SCHOOLS: List[Dict] = [
    {
        "school_id": "SCH_YIJING_ZHULUN",
        "name": "以經注論派",
        "paradigm": "以《內經》《難經》理論注解仲景條文，重病機闡釋",
        "members": [
            {"name": "成無己", "book_dirs": ["註解傷寒論", "傷寒明理論", "傷寒明理論_1"],
             "role": "創派（傷寒全注第一家）"},
        ],
        "scope": "適於病機層面的條文解釋；易把《內經》框架外推到仲景本義之外",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_CUOJIAN",
        "name": "錯簡重訂派",
        "paradigm": "認為王叔和編次已亂仲景舊貌，主張重訂條文編次（風傷衛/寒傷營/風寒兩傷框架）",
        "members": [
            {"name": "方有執", "book_dirs": ["傷寒論條辨", "傷寒論條辨_1"], "role": "創派"},
            {"name": "張璐", "book_dirs": ["傷寒纘論", "傷寒纘論_傷寒緒論"], "role": "承喻昌三綱之說"},
        ],
        "scope": "適於文獻編次與體例研究；重訂本身缺乏版本學直接證據",
        "opposed_to": ["SCH_WEIHU_JIULUN"],
    },
    {
        "school_id": "SCH_WEIHU_JIULUN",
        "name": "維護舊論派",
        "paradigm": "尊王叔和整理與成無己舊注，反對重訂編次",
        "members": [
            {"name": "張卿子", "book_dirs": ["張卿子傷寒論"], "role": "以成注為底本集注"},
            {"name": "陳念祖", "book_dirs": ["傷寒醫訣串解"], "role": "陳修園，尊經維舊"},
        ],
        "scope": "適於保守文本立場的解讀；對編次疑點回應不足",
        "opposed_to": ["SCH_CUOJIAN"],
    },
    {
        "school_id": "SCH_FANGZHENG",
        "name": "辨證論治·以方類證派",
        "paradigm": "以方名證、方證相應：按方劑統攝條文，重症狀組合與方劑對應",
        "members": [
            {"name": "柯琴", "book_dirs": ["傷寒來蘇集", "傷寒論注", "傷寒論翼", "傷寒附翼"],
             "role": "創派（《來蘇集》以方類證）"},
            {"name": "徐大椿", "book_dirs": ["傷寒論類方", "傷寒論類方_1"],
             "role": "徐靈胎，《類方》不類經而類方"},
        ],
        "scope": "適於方證對應與臨床檢索；對六經病機的系統性論述較弱",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_YIFA_LEIZHENG",
        "name": "辨證論治·以法類證派",
        "paradigm": "以治法統攝條文（正治/權變/斡旋/救逆/類病），重法度源流",
        "members": [
            {"name": "尤怡", "book_dirs": ["傷寒貫珠集", "傷寒貫珠集_1"], "role": "創派"},
            {"name": "錢潢", "book_dirs": ["傷寒溯源集"], "role": "溯源審因，以因類證"},
        ],
        "scope": "適於治法源流與法度研究",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_QIHUA",
        "name": "氣化學派",
        "paradigm": "以六氣氣化（標本中氣）解六經，重運氣一元論",
        "members": [
            {"name": "黃元御", "book_dirs": ["傷寒懸解"], "role": "一氣周流、土樞四象框架"},
        ],
        "scope": "理論自洽性強；框架先行，易過度統一化仲景本文",
        "opposed_to": ["SCH_FANGZHENG"],
    },
    {
        "school_id": "SCH_HEJIAN",
        "name": "河間學派（寒涼派）",
        "paradigm": "六氣皆從火化，傷寒多熱證，主寒涼清熱（溫病學先聲）",
        "members": [
            {"name": "劉完素", "book_dirs": ["傷寒直格_1", "傷寒標本心法類萃",
                                          "傷寒標本心法類萃_1", "傷寒心要_1"], "role": "創派"},
            {"name": "馬宗素", "book_dirs": ["劉河間傷寒醫鑑"], "role": "傳河間之學"},
            {"name": "鎦洪", "book_dirs": ["河間傷寒心要"], "role": "傳河間之學"},
        ],
        "scope": "適於熱病/溫病視角；以火熱立論對虛寒證候解釋力弱",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_KAOZHENG",
        "name": "考證學派（日本漢方考據）",
        "paradigm": "文獻考據與版本校勘，重異文、訓詁與出典",
        "members": [
            {"name": "丹波元簡", "book_dirs": ["傷寒論輯義"], "role": "《輯義》集諸注而考辨"},
            {"name": "丹波元胤", "book_dirs": ["中寒論辯證廣注"], "role": "承家學"},
        ],
        "scope": "適於版本異文與出典考證；臨床發揮非其所長",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_SONG_LINZHENG",
        "name": "宋代傷寒臨證派",
        "paradigm": "以證類方、以歌訣類證普及仲景學（傷寒九十論為現存最早醫案體）",
        "members": [
            {"name": "許叔微", "book_dirs": ["傷寒九十論", "傷寒發微論", "傷寒百證歌"],
             "role": "以醫案證經方"},
            {"name": "龐安石", "book_dirs": ["傷寒總病論", "傷寒總病論_1"], "role": "龐安時，廣其治法"},
            {"name": "郭雍", "book_dirs": ["仲景傷寒補亡論"], "role": "補亡輯佚"},
        ],
        "scope": "適於早期傳承與醫案研究",
        "opposed_to": [],
    },
    {
        "school_id": "SCH_JINGFANG_SHIYAN",
        "name": "近代經方實驗派",
        "paradigm": "以臨床實案驗證經方方證，主張方證對應的實效性",
        "members": [
            {"name": "曹穎甫", "book_dirs": ["經方實驗錄", "曹氏傷寒金匱發微合刊"],
             "role": "《經方實驗錄》百年實案"},
        ],
        "scope": "適於方證外部效度研究（本庫醫案回放基準即用其實案）",
        "opposed_to": [],
    },
]

BASIS_NOTE = ("學派歸屬為編輯性元數據（source_level=posthoc_induction），"
              "依據通行傷寒學術史分類整編，僅收語料在庫著者；"
              "agreement 字段來自注家分歧圖譜的實測一致度矩陣，"
              "用於數據層面檢驗學派分野。")


def _commentator_school() -> Dict[str, str]:
    out = {}
    for s in SCHOOLS:
        for m in s["members"]:
            out[m["name"]] = s["school_id"]
    return out


def build_school_registry() -> Dict:
    """構建學派註冊表並回填分歧圖譜的一致度證據。"""
    atlas_path = config.RESEARCH_DIR / "commentary_divergence.json"
    matrix = []
    if atlas_path.exists():
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
        matrix = atlas.get("agreement_matrix", [])
    member_school = _commentator_school()

    schools = []
    for s in SCHOOLS:
        names = {m["name"] for m in s["members"]}
        intra, cross = [], []
        for row in matrix:
            a, b = row.get("a", ""), row.get("b", "")
            pair = {"a": a, "b": b,
                    "mean_term_agreement": row.get("mean_term_agreement", 0.0),
                    "n_shared_clauses": row.get("n_shared_clauses", 0)}
            if a in names and b in names:
                intra.append(pair)
            elif (a in names) != (b in names) and (a in member_school and b in member_school):
                cross.append(pair)
        cross.sort(key=lambda p: p["mean_term_agreement"])
        entry = dict(s)
        entry["source_level"] = "posthoc_induction"
        entry["agreement"] = {
            "intra_school_pairs": intra,
            "most_divergent_cross_pairs": cross[:3],
        }
        schools.append(entry)

    return {"note": BASIS_NOTE,
            "n_schools": len(schools),
            "commentator_school": {k: member_school[k] for k in sorted(member_school)},
            "schools": schools}
