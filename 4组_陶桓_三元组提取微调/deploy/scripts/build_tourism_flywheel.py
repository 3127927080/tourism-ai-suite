"""旅游数据飞轮：按城市结构骨架生成「句子 + 12 类三元组」样本。

策略：
  - 城市与景点清单硬编码（真实知名，降低幻觉）
  - LLM 只负责组句与标注，禁止写票价/时长/班次/营业时间
  - 规则校验：关系在白名单、实体在句中、去重
  - 仅保留结构性高层事实

用法:
    python scripts/build_tourism_flywheel.py --cities 1 --per-city 30   # 试跑 1 城
    python scripts/build_tourism_flywheel.py                            # 全量 35 城
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
# 优先本项目的 .env，其次知识库项目的 .env（happycode 等共享配置）
ENV_CANDIDATES = [ROOT / ".env", Path(r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\.env")]
OUT_DIR = ROOT / "data" / "flywheel"

RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]

# 35 城 + 真实知名景点/片区（结构骨架的事实来源）
CITIES = {
    "北京": ["故宫", "天安门广场", "八达岭长城", "颐和园", "天坛", "南锣鼓巷", "什刹海", "鸟巢", "后海", "王府井"],
    "上海": ["外滩", "豫园", "上海迪士尼", "南京路", "田子坊", "东方明珠", "朱家角", "武康路", "陆家嘴"],
    "西安": ["兵马俑", "大雁塔", "西安城墙", "回民街", "华清池", "大唐不夜城", "华山", "钟楼", "陕西历史博物馆"],
    "重庆": ["洪崖洞", "解放碑", "磁器口", "长江索道", "武隆喀斯特", "大足石刻", "李子坝", "南山一棵树"],
    "成都": ["宽窄巷子", "锦里", "杜甫草堂", "大熊猫繁育研究基地", "春熙路", "都江堰", "青城山", "人民公园"],
    "杭州": ["西湖", "灵隐寺", "西溪湿地", "河坊街", "千岛湖", "雷峰塔", "九溪烟树", "龙井村"],
    "苏州": ["拙政园", "留园", "平江路", "山塘街", "虎丘", "周庄", "同里", "苏州博物馆"],
    "南京": ["中山陵", "夫子庙", "秦淮河", "总统府", "玄武湖", "明孝陵", "先锋书店", "老门东"],
    "青岛": ["栈桥", "八大关", "崂山", "五四广场", "金沙滩", "青岛啤酒博物馆", "小鱼山", "劈柴院"],
    "大连": ["星海广场", "老虎滩海洋公园", "滨海路", "金石滩", "棒棰岛", "东港", "俄罗斯风情街"],
    "厦门": ["鼓浪屿", "曾厝垵", "环岛路", "南普陀寺", "厦门大学", "中山路", "沙坡尾", "集美学村"],
    "广州": ["广州塔", "沙面岛", "陈家祠", "白云山", "长隆旅游度假区", "上下九", "北京路", "越秀公园"],
    "深圳": ["世界之窗", "欢乐谷", "大梅沙", "东部华侨城", "莲花山公园", "深圳湾公园", "南头古城"],
    "桂林": ["漓江", "阳朔西街", "象鼻山", "遇龙河", "龙脊梯田", "两江四湖", "十里画廊"],
    "昆明": ["滇池", "石林", "翠湖", "西山", "云南民族村", "金马碧鸡坊", "斗南花市"],
    "丽江": ["丽江古城", "玉龙雪山", "束河古镇", "泸沽湖", "拉市海", "黑龙潭", "白沙古镇"],
    "大理": ["大理古城", "洱海", "崇圣寺三塔", "双廊", "喜洲古镇", "苍山", "才村"],
    "三亚": ["亚龙湾", "天涯海角", "南山文化旅游区", "蜈支洲岛", "海棠湾", "大东海", "三亚湾", "鹿回头"],
    "张家界": ["张家界国家森林公园", "天门山", "袁家界", "金鞭溪", "黄龙洞", "宝峰湖", "凤凰古城"],
    "黄山": ["黄山风景区", "宏村", "西递", "屯溪老街", "徽州古城", "呈坎", "新安江"],
    "哈尔滨": ["中央大街", "圣索菲亚教堂", "冰雪大世界", "太阳岛", "亚布力滑雪场", "松花江", "老道外"],
    "长白山": ["长白山天池", "长白山北坡", "长白山西坡", "魔界风景区", "锦江大峡谷", "二道白河"],
    "拉萨": ["布达拉宫", "大昭寺", "八廓街", "纳木错", "罗布林卡", "色拉寺", "羊卓雍措"],
    "乌鲁木齐": ["天山天池", "新疆国际大巴扎", "红山公园", "南山牧场", "水磨沟"],
    "敦煌": ["莫高窟", "鸣沙山月牙泉", "雅丹国家地质公园", "玉门关", "阳关", "敦煌古城"],
    "洛阳": ["龙门石窟", "白马寺", "老君山", "洛邑古城", "关林", "洛阳博物馆"],
    "开封": ["清明上河园", "龙亭公园", "开封府", "大相国寺", "铁塔公园", "包公祠"],
    "武汉": ["黄鹤楼", "东湖", "户部巷", "湖北省博物馆", "武汉大学", "昙华林", "长江大桥"],
    "长沙": ["岳麓山", "橘子洲", "太平老街", "湖南省博物馆", "长沙世界之窗", "文和友", "天心阁"],
    "凤凰古城": ["沱江", "虹桥", "沈从文故居", "南华山", "跳岩", "东门城楼"],
    "婺源": ["篁岭", "江湾", "李坑", "思溪延村", "月亮湾", "晓起"],
    "呼伦贝尔": ["呼伦湖", "满洲里国门", "额尔古纳湿地", "莫日格勒河", "室韦", "根河湿地"],
    "兰州": ["中山桥", "黄河母亲雕塑", "白塔山公园", "甘肃省博物馆", "正宁路夜市", "拉卜楞寺"],
    "西宁": ["青海湖", "塔尔寺", "茶卡盐湖", "日月山", "东关清真大寺", "门源油菜花"],
    "天津": ["五大道", "意式风情区", "古文化街", "瓷房子", "天津之眼", "天津之眼摩天轮", "西开教堂"],
}

SYSTEM_PROMPT = (
    "你是旅游知识库的数据标注员。根据给定的城市与真实景点列表，生成自然、准确的中文短句，"
    "并为每句标注实体关系三元组。\n"
    f"关系只能从以下 12 个词中逐字选用（不得改写、不得新增）：{'、'.join(RELATIONS)}。\n"
    "注意：表示所属城市请用「位于」，表示类型/类别请用「属于」，不要写「所在城市」「类型」「功能」等词。\n"
    "关系使用指引：景点/片区归属城市或片区用「位于」；景点类别用「属于」；城市或线路包含景点用「包含」；"
    "两个地点距离近用「邻近」；线路经过某地用「途经」；城市或攻略推荐景点用「推荐」；适合某类人群用「适合」；"
    "以某特色或风貌闻名用「以…闻名」；需要预约或证件用「需要」；提供某项服务用「提供」；游览安排建议用「建议」；"
    "同类景点对比用「对比」。\n"
    "每条句子尽量标注 2-3 个三元组，并尽量覆盖不同的关系，不要全部只用「位于」。\n"
    "硬性要求：\n"
    "1. 头实体、尾实体必须严格出现在原句中（连续子串）；\n"
    "2. 只写结构性事实（归属、包含、邻近、适合人群、闻名特色、推荐、途经）；\n"
    "3. 严禁编造票价、营业时间、车程、班次、精确数字；\n"
    "4. 每个景点使用列表中给出的真实名称，不要臆造新景点；\n"
    "5. 句子 20-60 字，像真实旅游攻略。\n"
    "输出格式：每行一条，字段之间用三个竖线 ||| 分隔，不要编号、不要表格、不要 JSON、不要解释。\n"
    "示例：\n"
    "亚龙湾位于三亚，以海湾沙滩闻名，适合亲子游客。 ||| (亚龙湾, 位于, 三亚) ||| (亚龙湾, 以…闻名, 海湾沙滩) ||| (亚龙湾, 适合, 亲子游客)\n"
    "故宫和天安门广场都位于北京中轴线上，天安门广场邻近故宫。 ||| (故宫, 位于, 北京中轴线) ||| (天安门广场, 位于, 北京中轴线) ||| (天安门广场, 邻近, 故宫)"
)

_write_lock = threading.Lock()


def load_env() -> dict:
    """先读本项目 .env，缺失项回落知识库 .env。"""
    vals = {}
    for path in reversed(ENV_CANDIDATES):  # 后面的先读，前面的覆盖
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def normalize(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def clean_triples(items, sentence: str) -> list[list[str]]:
    norm_s = normalize(sentence)
    out, seen = [], set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not (isinstance(it, (list, tuple)) and len(it) == 3):
            continue
        head, rel, tail = (str(x).strip() for x in it)
        if not head or not tail or rel not in RELATIONS:
            continue
        if normalize(head) not in norm_s or normalize(tail) not in norm_s:
            continue
        key = (head, rel, tail)
        if key in seen:
            continue
        seen.add(key)
        out.append([head, rel, tail])
    return out


def parse_items(raw: str) -> list[dict]:
    """多格式鲁棒解析：||| 行 > Markdown 表格 > 句子：分块 > JSON。"""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I).strip()
    text = text.replace("`", "")
    items = []

    def grab_triples(s: str):
        out = []
        for pm in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", s):
            parts = re.split(r"\s*[,，]\s*", pm.group(1))
            if len(parts) == 3:
                out.append([p.strip() for p in parts])
        return out

    # 1) ||| 单行格式
    for line in text.splitlines():
        if "|||" not in line:
            continue
        cols = [c.strip() for c in line.split("|||") if c.strip()]
        if len(cols) >= 2:
            sentence = cols[0]
            triples = [t for c in cols[1:] for t in grab_triples(c)]
            if sentence and triples:
                items.append({"sentence": sentence, "triples": triples})
    if items:
        return items

    # 2) Markdown 表格：| 序号 | 句子 | 三元组 |
    for line in text.splitlines():
        if line.count("|") < 3:
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        sentence = re.sub(r"^\d+[.、]?\s*", "", cols[1]).strip()
        triples = grab_triples(cols[-1])
        if sentence and triples and not set(sentence) <= set("-: "):
            items.append({"sentence": sentence, "triples": triples})
    if items:
        return items

    # 3) 句子：分块
    for block in re.split(r"(?=句子\s*[：:])", text):
        sm = re.search(r"句子\s*[：:]\s*(.+)", block)
        if not sm:
            continue
        sentence = re.split(r"\n|三元组", sm.group(1).strip())[0].strip()
        triples = grab_triples(block)
        if sentence and triples:
            items.append({"sentence": sentence, "triples": triples})
    if items:
        return items

    # 4) JSON 回退
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:
        return []
    return [x for x in arr if isinstance(x, dict) and x.get("sentence")]


def make_client(env: dict):
    """优先 DeepSeek 官方（格式更稳、非推理模型），否则回落 happycode 网关。"""
    if env.get("DEEPSEEK_API_KEY"):
        return (OpenAI(api_key=env["DEEPSEEK_API_KEY"],
                       base_url=env.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com",
                       timeout=180, max_retries=0),
                env.get("DEEPSEEK_MODEL", "deepseek-chat"), "deepseek-official")
    return (OpenAI(api_key=env.get("LLM_API_KEY") or env.get("ALIYUN_API_KEY"),
                   base_url=env.get("LLM_API_BASE"), timeout=180, max_retries=0),
            env.get("LLM_DEFAULT_MODEL"), "happycode")


def gen_city(client, model, city: str, spots: list[str], per_city: int, chunk: int = 10):
    """分小批调用（长输出易导致格式漂移），按句子去重。"""
    seen, out = set(), []
    rounds = max(1, per_city // chunk)
    for r_i in range(rounds):
        user = (
            f"城市：{city}\n"
            f"真实景点/片区列表：{'、'.join(spots)}\n"
            f"请生成 {chunk} 条句子及其三元组，尽量覆盖不同的景点和不同的关系。"
        )
        for attempt in range(3):
            try:
                r = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": user}],
                    temperature=0.3,
                )
                items = parse_items(r.choices[0].message.content or "")
                for it in items:
                    sent = str(it["sentence"]).strip()
                    if not sent or sent in seen:
                        continue
                    triples = clean_triples(it.get("triples"), sent)
                    if triples:
                        seen.add(sent)
                        out.append({"city": city, "sentence": sent, "triples": triples})
                break
            except Exception:
                time.sleep(min(2 ** attempt, 6))
    return city, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", type=int, default=0, help="0 表示全部")
    ap.add_argument("--per-city", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--output", default=str(OUT_DIR / "flywheel_raw.jsonl"))
    args = ap.parse_args()

    env = load_env()
    client, model, provider = make_client(env)

    city_items = list(CITIES.items())
    if args.cities:
        city_items = city_items[: args.cities]
    print(f"provider={provider} model={model} 城市数={len(city_items)} 每城={args.per_city}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    all_rows, total = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(gen_city, client, model, c, s, args.per_city) for c, s in city_items]
        for fut in as_completed(futures):
            city, rows = fut.result()
            total += len(rows)
            all_rows.extend(rows)
            print(f"  {city}: 保留 {len(rows)} 条")

    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"✅ 共 {total} 条 -> {out_path}")


if __name__ == "__main__":
    main()
