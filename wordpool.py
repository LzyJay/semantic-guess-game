# 词池：种子词（含分类）+ 自动归类 + 出题 + 提示词选取
# 种子词来源：2026-10-13 晚抖音原版实录答案 + 常用词；其余词按"最近种子词"自动归类。
import random
import re

from engine import load_common_words

CJK = re.compile(r"^[\u4e00-\u9fff]+$")

CATEGORIES = ["行为动作", "日常物品", "身份人际", "描述形容", "天文地理",
              "植物", "动物", "食物饮品", "抽象概念", "文字"]

# 上位词（场景级词）：提示一优先用它——"都是XX"就是最直接的推理链
HYPERNYMS = ["餐具", "厨具", "文具", "工具", "家具", "水果", "蔬菜", "粮食", "主食",
             "花卉", "动物", "宠物", "昆虫", "鸟类", "日化", "化妆品", "饮品", "调料",
             "乐器", "衣物", "鞋帽", "景观", "海滨", "家庭", "亲人", "车辆", "道路",
             "建筑", "电器", "灯具", "寝具", "玩具", "书籍", "运动", "天气", "水产",
             "肉类", "甜食", "坚果", "豆类", "药材", "礼品", "语言", "文学", "情感"]

SEED = {
    "行为动作": "进展 奔腾 奔流 争议 增加 改变 庆祝 劳动 复制 删除 搜索 分享 查看 审阅 放映 书写 拥抱 追逐 躲藏 思考 回忆 计划 准备 收拾 修理 雕刻 灌溉 收获 播种 张望 倾听 呼喊 微笑 哭泣 跌倒 攀登 游泳 飞翔 爬行 挖掘 摇晃 折叠 撕碎 涂抹 捆绑 搬运 交换 赠送 归还 申请 拒绝 同意 反对 支持 鼓励 安慰 责备 原谅 忘记 记得 理解 误会 解释 隐瞒 揭露 模仿 创造 破坏 修复 治疗 诊断 预防 实验 观察 测量 计算 记录 整理 分类 比较 选择 决定 放弃 坚持 妥协 竞争 合作 分工 协调 指挥 服从 监督 检查 审批 签署 发布 通知 报告 汇报 申请 审批",
    "日常物品": "开罐器 垃圾桶 遥控器 充电器 剪刀 尺子 铅笔 橡皮 书包 雨伞 钥匙 灯泡 插头 电池 镜子 梳子 牙刷 毛巾 香皂 洗发水 洗衣液 抹布 衣架 针线 纽扣 拉链 口袋 帽子 围巾 手套 腰带 鞋子 拖鞋 皮鞋 凉鞋 靴子 睡衣 浴巾 床单 被子 枕头 床垫 沙发 茶几 板凳 桌子 椅子 书柜 衣柜 抽屉 相框 花瓶 烛台 打火机 烟灰缸 杯子 盘子 碗筷 勺子 叉子 刀子 案板 冰箱 烤箱 微波炉 电饭煲 水壶 茶壶 茶杯 酒瓶 罐子 盒子 袋子 箱子 篮子 筛子 漏斗 锯子 锤子 螺丝刀 钳子 扳手 卷尺 磨刀石 胶水 胶带 绳子 钉子 螺丝 磁铁 放大镜 显微镜 望远镜 手电筒 信号灯",
    "身份人际": "明星 演员 歌手 偶像 粉丝 邻居 同事 同学 朋友 闺蜜 兄弟 姐妹 夫妻 恋人 新娘 伴娘 园长 老师 学生 医生 护士 律师 法官 警察 军人 厨师 服务员 销售员 服务员 司机 导游 记者 编辑 作家 画家 音乐家 科学家 工程师 程序员 设计师 建筑师 会计师 翻译 导游 裁判 教练 徒弟 师傅 长辈 晚辈 亲戚 家人 父母 儿女 孙子 外孙 邻居 室友 搭档 对手 敌人 恩人 陌生人 名人 伟人 凡人 路人 主角 配角 观众 读者 乘客 游客 顾客 客户 老板 员工 下属 上司",
    "描述形容": "坚硬 柔软 光滑 粗糙 明亮 黑暗 寒冷 炎热 干燥 潮湿 肥胖 瘦小 高大 矮小 宽阔 狭窄 迅速 缓慢 清晰 模糊 浓厚 清淡 甜美 苦涩 酸臭 香醇 吵闹 安静 整洁 凌乱 干净 肮脏 新鲜 腐烂 昂贵 廉价 丰富 匮乏 深刻 浅薄 复杂 简单 困难 容易 危险 安全 稳定 动荡 诚实 虚伪 勇敢 胆怯 慷慨 吝啬 勤奋 懒惰 谦虚 骄傲 热情 冷漠 亲切 严厉 温柔 粗暴 机灵 愚钝 成熟 幼稚 稳重 轻浮 认真 马虎 耐心 急躁 仔细 粗心",
    "天文地理": "太阳 月亮 星星 地球 火星 金星 木星 土星 云层 山脉 河流 海洋 沙漠 森林 草原 峡谷 平原 高原 盆地 海峡 岛屿 冰川 火山 地震 台风 暴雨 大雪 冰雹 彩虹 闪电 极光 日落 日出 黄昏 黎明 子夜 正午 朝霞 晚霞 天空 大地 海洋 湖泊 池塘 溪流 瀑布 泉水 井水 海水 沙滩 海岸 码头 桥梁 隧道 铁路 公路 街道 广场 花园 公园 校园 工厂 农村 城市 郊区 市中心 火车站 飞机场 港口 车站 路口 红绿灯 斑马线",
    "植物": "杜鹃 玫瑰 牡丹 茉莉 兰花 菊花 梅花 桃花 樱花 荷花 睡莲 桂花 水仙 仙人掌 绿萝 多肉 盆栽 大树 小草 苔藓 蘑菇 松树 柳树 杨树 桦树 樟树 枫树 银杏 芭蕉 竹子 藤蔓 荆棘 海藻 浮萍 荷叶 莲蓬 莲子 树枝 树干 树根 树叶 花瓣 花蕊 花粉 花苞 果实 种子 幼苗 根系 萌芽 凋谢 盛开 枯萎 种植 浇水 施肥 修剪 采摘",
    "动物": "熊猫 老虎 狮子 大象 长颈鹿 斑马 猴子 猩猩 兔子 松鼠 刺猬 蝙蝠 海豚 鲨鱼 章鱼 企鹅 鸵鸟 孔雀 老鹰 麻雀 蚂蚁 蜜蜂 蝴蝶 蜻蜓 螳螂 蟋蟀 蜗牛 蚯蚓 蜘蛛 蝎子 螃蟹 龙虾 带鱼 鲤鱼 草鱼 鲫鱼 金鱼 热带鱼 青蛙 蟾蜍 壁虎 蜥蜴 蟒蛇 眼镜蛇 乌龟 海龟 鳄鱼 河马 犀牛 棕熊 北极熊 狼 狐狸 野猪 鹿 羊 马 驴 牛 猪 狗 猫 鸡 鸭 鹅 鸽子 燕子 乌鸦 喜鹊 黄鹂 鹦鹉 八哥 猫头鹰 啄木鸟 天鹅 仙鹤 火烈鸟",
    "食物饮品": "米饭 面条 馒头 包子 饺子 馄饨 火锅 烧烤 龙虾 螃蟹 西瓜 苹果 香蕉 葡萄 草莓 橙子 桃子 李子 杏子 樱桃 菠萝 椰子 芒果 荔枝 龙眼 榴莲 咖啡 牛奶 豆浆 果汁 可乐 绿茶 红茶 奶茶 汽水 矿泉水 啤酒 白酒 红酒 米酒 面包 蛋糕 饼干 糖果 巧克力 冰淇淋 薯片 爆米花 火腿 香肠 烤肉 炖汤 炒菜 凉拌 清蒸 红烧 油炸 糖醋 沙拉 三明治 汉堡 披萨",
    "抽象概念": "感情 思想 思维 记忆 梦想 希望 时间 空间 生命 自由 和平 战争 幸福 痛苦 勇敢 诚实 善良 智慧 力量 速度 秩序 混乱 缘分 命运 轮回 因果 逻辑 真理 谎言 秘密 诺言 信仰 哲学 科学 艺术 文化 历史 传统 习俗 法律 规则 权利 义务 责任 荣誉 尊严 价值 意义 目标 计划 过程 结果 原因 条件 方法 效率 质量 数量 程度 范围 边界 核心 关键 细节 整体 局部",
    "文字": "汉字 拼音 笔画 偏旁 部首 标点 逗号 句号 问号 感叹号 冒号 分号 省略号 破折号 书名号 楷书 行书 草书 隶书 篆书 字体 字形 字义 字音 词典 字典 辞典 成语 俗语 谚语 歇后语 对联 诗歌 散文 小说 剧本 寓言 神话 传说 故事 童话 文言文 白话文 字母 单词 句子 段落 章节 标题 目录 索引 注释 附录",
}

CAT_KEYWORDS = {}  # 预留：关键词兜底（未用，自动归类已够用）


class WordPool:
    def __init__(self, engine):
        self.engine = engine
        self.seed_words = []
        self.seed_vec = {}
        for cat, text in SEED.items():
            for w in text.split():
                if engine.has(w):
                    self.seed_words.append((w, cat))
        import numpy as np
        self._seed_idx = np.array([engine.index[w] for w, _ in self.seed_words])
        self._seed_cat = [c for _, c in self.seed_words]

    def categorize(self, word):
        """返回 (分类, 置信度)：取引擎最近种子词的分类。"""
        i = self.engine.index.get(word)
        if i is None:
            return None, 0.0
        sims = self.engine.unit[self._seed_idx] @ self.engine.unit[i]
        j = int(sims.argmax())
        return self._seed_cat[j], round(float(sims[j]) * 100, 1)

    def seed_pool(self, n_chars):
        """可作答案的词：种子词里字数匹配的（分类已知、常见、可玩）。"""
        return [(w, c) for w, c in self.seed_words if len(w) == n_chars]

    def hint_word_in_band(self, answer, category, exclude, lo, hi):
        """按分数段取提示词（原版口径：提示一 60–70、提示二 70–80，逐级逼近）。
        候选来源（合并后统一排序）：上位词表 ∪ 答案近邻簇∩常用词 ∪ 同分类种子词 ∪ 任意种子词。
        硬过滤：2-4 字、纯中文、**不含答案的字**（防透题，首字留给提示三）、落在 [lo,hi] 段内。
        排序：两字优先 → **簇内平均相似度**（候选词与答案近邻簇的平均分，高＝与答案同域同套组，
        提示词才带可推理的逻辑关系）→ 接近段中值。"""
        target = (lo + hi) / 2
        common = load_common_words()

        def ok(w, need_common):
            # 只挡"首字"（提示三要揭的那个字），不挡尾字——否则"叉子"会把勺子/钳子全滤掉
            return (w != answer and w not in exclude and 2 <= len(w) <= 4 and CJK.match(w)
                    and answer[0] not in w
                    and (not need_common or not common or w in common))

        # 答案的近邻簇（同套组词）——逻辑关系的度量基准
        cluster = [w for w, _ in self.engine.nearest(answer, k=12) if w != answer]

        def cluster_avg(w):
            """w 与答案近邻簇的平均相似度：越高＝越和答案同属一个场景/套组。"""
            if not cluster:
                return 0.0
            return sum(self.engine.similarity(w, n) or 0.0 for n in cluster) / len(cluster)

        cands = {}
        # 上位词：只认"最佳上位词"（与答案最像的那个），且必须落在段内——最佳上位词不在段内
        # 说明它属于另一档，硬用次优上位词会误导（如给老虎配"昆虫"）
        best_hyper = None
        for h in HYPERNYMS:
            if ok(h, True):
                s = self.engine.similarity(h, answer)
                if s is not None and (best_hyper is None or s > best_hyper[1]):
                    best_hyper = (h, s)
        if best_hyper and lo <= best_hyper[1] <= hi:
            return best_hyper                      # "都是XX"＝最直接的推理链
        # 种子词（同分类优先，任意种子兜底；本身是策展常用词，自然、不生僻）
        for w, c in self.seed_words:
            if ok(w, False):
                s = self.engine.similarity(w, answer)
                if s is not None:
                    cands.setdefault(w, s)

        in_band = [(w, s) for w, s in cands.items() if lo <= s <= hi]
        if in_band:
            in_band.sort(key=lambda x: (0 if len(x[0]) == 2 else 1,
                                        -cluster_avg(x[0]), abs(x[1] - target)))
            return in_band[0]
        # 段内一个都没有 → 全源里最接近段中值的（放宽"不含答案的字"）
        allc = [(w, s) for w, s in cands.items()]
        if not allc:
            for w, c in self.seed_words:
                if w != answer and w not in exclude and 2 <= len(w) <= 4 and CJK.match(w):
                    s = self.engine.similarity(w, answer)
                    if s is not None:
                        allc.append((w, s))
        if allc:
            allc.sort(key=lambda x: (0 if len(x[0]) == 2 else 1, abs(x[1] - target)))
            return allc[0]
        return None
