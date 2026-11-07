-- ============================================================================
-- 日照房价探针 / 捡漏雷达 —— SQLite 表结构
-- 设计原则：先把低价当嫌疑，再判断是否值得看
-- 所有金额单位：元；面积单位：㎡；单价单位：元/㎡
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 1. 小区（板块的基础盘，流动性来自成交活跃度，短期先用人工/统计口径分）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS community (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,                 -- 小区名
    district          TEXT    NOT NULL,                 -- 板块：新市区/东港区/石臼/老城/山海天/高新区/开发区/莒县/五莲/岚山
    is_extended       INTEGER NOT NULL DEFAULT 0,       -- 是否扩展区（流动性打折）
    lat               REAL,                             -- 纬度（地图用）
    lng               REAL,                             -- 经度（地图用）
    build_year        INTEGER,                          -- 建成年代
    has_elevator      INTEGER NOT NULL DEFAULT 1,       -- 是否有电梯
    school_district   TEXT,                             -- 对口学校（学区参考）
    liquidity_score   REAL    NOT NULL DEFAULT 60,      -- 板块/个盘流动性 0-100（越高越好转手）
    deal_active_90d   INTEGER NOT NULL DEFAULT 0,       -- 近90天该小区成交套数（成交活跃度）
    created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (name, district)
);

-- ---------------------------------------------------------------------------
-- 2. 二手房挂牌 listing
--    fingerprint = md5(source + 小区 + 户型 + 面积(取整) + 总价(取整))
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS listing (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint        TEXT    NOT NULL UNIQUE,         -- 去重指纹
    source             TEXT    NOT NULL,                -- fangtianxia / anjuke / zhiye ...
    source_url         TEXT,
    community_id       INTEGER REFERENCES community(id),
    community_name     TEXT    NOT NULL,
    district           TEXT    NOT NULL,
    title              TEXT,

    layout             TEXT,                            -- 户型原文，如 "3室2厅2卫"
    rooms              INTEGER,                         -- 室数
    halls              INTEGER,                         -- 厅数
    area               REAL    NOT NULL,                -- 建筑面积 ㎡
    total_price        REAL    NOT NULL,                -- 总价（万元→元 已换算）
    unit_price         REAL    NOT NULL,                -- 单价 元/㎡

    floor_text         TEXT,                            -- 楼层原文
    floor_no           INTEGER,
    floor_total        INTEGER,
    orientation        TEXT,                            -- 朝向
    decoration         TEXT,                            -- 装修：毛坯/简装/精装
    build_year         INTEGER,
    age                INTEGER,                         -- 房龄（年）
    has_elevator       INTEGER NOT NULL DEFAULT 1,

    first_price        REAL,                            -- 首次挂牌总价
    first_seen         TEXT,                            -- 首次出现日期
    last_seen          TEXT,                            -- 最近一次抓到日期
    list_days          INTEGER NOT NULL DEFAULT 0,      -- 挂牌天数
    price_changes      INTEGER NOT NULL DEFAULT 0,      -- 累计调价次数
    consecutive_drops  INTEGER NOT NULL DEFAULT 0,      -- 连续降价次数
    last_drop_days_ago INTEGER,                         -- 最近一次降价距今天数

    defect_tags        TEXT    NOT NULL DEFAULT '[]',   -- 硬伤标签 JSON: ["顶楼","临街"]
    rent_month         REAL,                            -- 同户型月租金挂牌中位数（元/月）

    status             TEXT    NOT NULL DEFAULT 'active', -- active / delisted / sold
    raw_json           TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_listing_community ON listing(community_id, layout, area);
CREATE INDEX IF NOT EXISTS idx_listing_district  ON listing(district, status);
CREATE INDEX IF NOT EXISTS idx_listing_price     ON listing(unit_price);

-- ---------------------------------------------------------------------------
-- 3. 调价向量 / 成交参考历史（首次价 → 当前价 → 每次调价幅度与间隔）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    price        REAL    NOT NULL,                      -- 该次观测的总价
    unit_price   REAL,                                  -- 对应单价
    observed_at  TEXT    NOT NULL,                      -- 观测日期 YYYY-MM-DD
    drop_pct     REAL    NOT NULL DEFAULT 0,            -- 相对上一次的降幅（负数表示降价）
    days_gap     INTEGER,                               -- 距上一次调价间隔天数
    kind         TEXT    NOT NULL DEFAULT 'listing',    -- listing 挂牌 / deal 成交（如有成交字段）
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_hist_listing ON price_history(listing_id, observed_at);

-- ---------------------------------------------------------------------------
-- 4. 法拍 auction
--    到手价 = 起拍价 + 税费预估 + 欠费预估 + 维修预留
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auction (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint           TEXT    NOT NULL UNIQUE,      -- md5(source+标题+起拍价)
    source                TEXT    NOT NULL,             -- ali / jd
    source_url            TEXT,
    title                 TEXT    NOT NULL,
    community_name        TEXT,
    district              TEXT,
    area                  REAL,
    layout                TEXT,

    appraisal_price       REAL,                         -- 评估价
    start_price           REAL    NOT NULL,             -- 起拍价
    stage                 TEXT,                         -- 一拍 / 二拍 / 变卖
    auction_date          TEXT,                         -- 开拍日期
    deposit               REAL,                         -- 保证金

    tax_est               REAL    NOT NULL DEFAULT 0,   -- 税费预估（含契税/增值税/个税，法拍常由买方全担）
    arrears_est           REAL    NOT NULL DEFAULT 0,   -- 物业/水电/供暖欠费预估
    repair_reserve        REAL    NOT NULL DEFAULT 0,   -- 维修预留
    landing_price         REAL,                         -- 到手价预估（上面三项之和 + 起拍）

    comparable_unit_price REAL,                         -- 同小区二手可比单价（人工/引擎回填）
    deal_price            REAL,                         -- 实际成交价（拍卖结束后回填；NULL=未回填/流拍未知）
    deal_date             TEXT,                         -- 成交日期 YYYY-MM-DD
    start_discount        REAL,                         -- 起拍折扣率 = 1 - 起拍/评估
    net_discount          REAL,                         -- 捡漏净折扣 = 1 - (到手单价 / 可比单价)

    risk_tags             TEXT    NOT NULL DEFAULT '[]',-- ["占用","租赁","腾退难","税费自担","产权瑕疵","欠费","限购"]
    risk_level            TEXT    NOT NULL DEFAULT 'mid', -- low / mid / high
    status                TEXT    NOT NULL DEFAULT 'active',
    raw_json              TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_auction_district ON auction(district, status);

-- ---------------------------------------------------------------------------
-- 5. 异动事件表（每日增量计算产出）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS anomaly_event (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key      TEXT    NOT NULL UNIQUE,             -- 去重键: 类型|对象|日期
    event_date     TEXT    NOT NULL,                    -- YYYY-MM-DD
    target_type    TEXT    NOT NULL,                    -- listing / auction
    target_id      INTEGER NOT NULL,
    community_name TEXT,
    district       TEXT,
    event_type     TEXT    NOT NULL,                    -- sudden_drop / deep_dip / long_listed / miskill / board_divergence / new_home / auction_deal
    severity       TEXT    NOT NULL DEFAULT 'white',    -- red / yellow / white
    total_price    REAL,
    unit_price     REAL,
    ref_unit_price REAL,                                -- 参照（同小区同户型近3月中位）
    drop_pct       REAL,                                -- 相对参照的偏离（正数=更低）
    detail_json    TEXT    NOT NULL DEFAULT '{}',       -- 指标明细，前端展示用
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_anomaly_date ON anomaly_event(event_date DESC, severity);
CREATE INDEX IF NOT EXISTS idx_anomaly_type ON anomaly_event(event_type, event_date DESC);

-- ---------------------------------------------------------------------------
-- 6. 捡漏评分表（0-100，透明可解释）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leak_score (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type    TEXT    NOT NULL,                    -- listing / auction
    target_id      INTEGER NOT NULL,
    score          REAL    NOT NULL,                    -- 总分
    s_price        REAL    NOT NULL,                    -- 价格偏离维度 0-100
    s_motive       REAL    NOT NULL,                    -- 调价动机维度
    s_defect       REAL    NOT NULL,                    -- 硬伤可控维度
    s_liquidity    REAL    NOT NULL,                    -- 流动性维度
    s_fit          REAL    NOT NULL,                    -- 自住适配维度
    confidence     TEXT    NOT NULL DEFAULT 'mid',      -- high / mid / low
    grade          TEXT    NOT NULL DEFAULT 'white',    -- red / yellow / white
    sample_n       INTEGER NOT NULL DEFAULT 0,          -- 可比样本数
    deviation_pct  REAL,                                -- 价格偏离度
    why_cheap      TEXT,                                -- 为什么便宜（假设，需核验）
    why_good       TEXT,                                -- 为什么可能是真漏
    why_trap       TEXT,                                -- 为什么可能是坑
    computed_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (target_type, target_id)
);

-- ---------------------------------------------------------------------------
-- 7. 观察清单
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watch_item (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type    TEXT    NOT NULL,                    -- listing / auction / community
    target_id      INTEGER NOT NULL,
    community_name TEXT,
    district       TEXT,
    note           TEXT,
    follow_status  TEXT    NOT NULL DEFAULT 'watching', -- watching / contacted / viewed / rejected / done
    base_price     REAL,                                -- 加入观察时的总价
    last_price     REAL,                                -- 最近价格（周报对比用）
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    last_checked   TEXT,
    UNIQUE (target_type, target_id)
);

-- ---------------------------------------------------------------------------
-- 8. 观察周报快照
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_report (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start   TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (week_start)
);

-- ---------------------------------------------------------------------------
-- 9. 推送订阅配置（单行配置，id 恒为 1）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enabled           INTEGER NOT NULL DEFAULT 1,       -- 总开关
    channel           TEXT    NOT NULL DEFAULT 'auto',  -- auto / wecom_webhook / wecom_app / mp_template / log
    min_score         REAL    NOT NULL DEFAULT 60,      -- 最低推送分数
    push_red          INTEGER NOT NULL DEFAULT 1,       -- 是否推红级
    push_yellow       INTEGER NOT NULL DEFAULT 1,       -- 是否推黄级
    red_realtime      INTEGER NOT NULL DEFAULT 1,       -- 红级实时推
    yellow_daily      INTEGER NOT NULL DEFAULT 1,       -- 黄级每日汇总（否则也实时）
    quiet_hours       TEXT    NOT NULL DEFAULT '23:00-07:00',
    dedup_hours       INTEGER NOT NULL DEFAULT 24,      -- 同房源去重窗口
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------------
-- 10. 推送日志（同时承担去重职责 + 站内铃铛消息源）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key      TEXT    NOT NULL UNIQUE,             -- 去重键: target_type|target_id|grade|价格|日期
    target_type    TEXT    NOT NULL,
    target_id      INTEGER NOT NULL,
    grade          TEXT    NOT NULL,                    -- red / yellow
    channel        TEXT    NOT NULL,                    -- 实际使用的通道
    status         TEXT    NOT NULL,                    -- sent / failed / skipped / logged
    title          TEXT,
    message        TEXT,
    response       TEXT,                                -- 通道返回内容（截断）
    is_read        INTEGER NOT NULL DEFAULT 0,          -- 站内铃铛已读标记
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_alert_created ON alert_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_target  ON alert_log(target_type, target_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 11. 抓取日志（异常隔离与可观测性：单站失败不影响整体）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    task          TEXT,                                 -- 二手房/法拍
    status        TEXT NOT NULL,                        -- ok / partial / failed / skipped
    items_found   INTEGER NOT NULL DEFAULT 0,
    items_saved   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    elapsed_ms    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------------
-- 12. 用户偏好（默认自住导向，可配置；影响自住适配维度）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_pref (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    district_pref   TEXT NOT NULL DEFAULT '["新市区","东港区","石臼"]',
    area_min        REAL NOT NULL DEFAULT 80,
    area_max        REAL NOT NULL DEFAULT 130,
    rooms_prefer    INTEGER NOT NULL DEFAULT 3,
    need_elevator   INTEGER NOT NULL DEFAULT 1,
    avoid_top_floor INTEGER NOT NULL DEFAULT 1,
    max_age         INTEGER NOT NULL DEFAULT 25,
    care_school     INTEGER NOT NULL DEFAULT 1,
    -- 购房成本测算参数（管理台可改，前端「月供/现金门槛」用）
    down_ratio      REAL NOT NULL DEFAULT 0.30,   -- 首付比例
    loan_years      INTEGER NOT NULL DEFAULT 30,  -- 贷款年限
    loan_rate       REAL NOT NULL DEFAULT 0.031,  -- 年利率（首套商贷参考）
    is_first_home   INTEGER NOT NULL DEFAULT 1,   -- 是否首套（影响契税）
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------------
-- 13. 新房（在售楼盘）
--     新房与周边二手倒挂，是自住者最值得看的一类「真便宜」，
--     但倒挂必须做毛坯/得房率调整，否则会把坑算成漏。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS new_home (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,                  -- 楼盘名
    developer       TEXT,                              -- 开发商
    district        TEXT    NOT NULL,                  -- 板块
    address         TEXT,                              -- 地址
    lat             REAL,
    lng             REAL,
    avg_unit_price  REAL    NOT NULL,                  -- 参考均价 元/㎡
    area_min        REAL,                              -- 在售最小面积
    area_max        REAL,                              -- 在售最大面积
    layout_text     TEXT,                              -- 主力户型
    delivery_date   TEXT,                              -- 预计交付 YYYY-MM
    decoration      TEXT,                              -- 毛坯 / 精装
    property_fee    REAL,                              -- 物业费 元/㎡·月
    plot_ratio      REAL,                              -- 容积率（越高越挤）
    green_ratio     REAL,                              -- 绿化率
    total_units     INTEGER,                           -- 总户数
    sale_status     TEXT    NOT NULL DEFAULT '在售',    -- 在售 / 尾盘 / 待售 / 售罄
    risk_tags       TEXT    NOT NULL DEFAULT '[]',     -- ["交付风险","教育配套未落地","容积率高"]
    source          TEXT,
    source_url      TEXT,
    note            TEXT,
    status          TEXT    NOT NULL DEFAULT 'active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_new_home_district ON new_home(district, status);

-- ---------------------------------------------------------------------------
-- 14. 运行期配置（管理台写入，覆盖 .env 的默认值；密钥只存密文摘要对外脱敏）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_setting (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    is_secret   INTEGER NOT NULL DEFAULT 0,   -- 1=敏感（接口回显时脱敏）
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------------
-- 15. 小区价格快照（关注小区房价波动用）
--     community 表刻意不冗余价格：价格随在售房源实时变动，落一份按日的
--     时间序列才谈得上「波动」。observed_date 唯一，重复跑管线只覆盖当天。
--     backfill 的历史点由 price_history 回推，data_kind 区分来源口径。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS community_price_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id    INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    observed_date   TEXT    NOT NULL,                  -- YYYY-MM-DD
    listing_n       INTEGER NOT NULL DEFAULT 0,       -- 当日样本套数
    median_unit     REAL,                             -- 中位挂牌单价 元/㎡
    avg_unit        REAL,
    min_unit        REAL,
    max_unit        REAL,
    target_area_n   INTEGER NOT NULL DEFAULT 0,       -- 120-150㎡ 在售套数
    data_kind       TEXT    NOT NULL DEFAULT 'daily', -- daily=当日全量 / backfill=历史回推
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (community_id, observed_date)
);
CREATE INDEX IF NOT EXISTS idx_comm_snapshot_date ON community_price_snapshot(observed_date);

-- ---------------------------------------------------------------------------
-- 17. 市场大盘日快照（全城口径，只统计真实采集数据）
--     listing_n / median_unit 来自当日 active 的 crawled 房源；
--     new_n 当日新挂牌；delist_n 当日判定下架（下架≠成交，是成交的保守代理）。
--     重复跑只覆盖当天。城市大盘仪表盘与去化近似都从这张表读。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    day          TEXT    NOT NULL,                       -- YYYY-MM-DD
    listing_n    INTEGER NOT NULL DEFAULT 0,             -- 当日在售（crawled）
    median_unit  REAL,                                   -- 全城中位挂牌单价 元/㎡
    new_n        INTEGER NOT NULL DEFAULT 0,             -- 当日新增挂牌
    delist_n     INTEGER NOT NULL DEFAULT 0,             -- 当日判定下架
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (day)
);

-- ---------------------------------------------------------------------------
-- 18. 租金样本（真实租售比用）
--     每周从贝壳日照租房页低频抓一批；租售比按「每小区最近一次采集」口径算，
--     旧批次保留作历史，不参与汇总。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rent_sample (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT    NOT NULL UNIQUE,
    source          TEXT    NOT NULL DEFAULT 'ke_zu',
    source_ref      TEXT,
    source_url      TEXT,
    community_id    INTEGER REFERENCES community(id),
    community_name  TEXT    NOT NULL,
    district        TEXT,
    layout          TEXT,
    area            REAL,
    rent_month      REAL    NOT NULL,                 -- 元/月
    rent_pm         REAL,                             -- 元/㎡·月
    collected_date  TEXT    NOT NULL,                 -- YYYY-MM-DD
    raw_json        TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rent_community ON rent_sample(community_id, collected_date);
CREATE INDEX IF NOT EXISTS idx_rent_date ON rent_sample(collected_date);

-- ---------------------------------------------------------------------------
-- 16. 真实房源导入批次（来源可追溯）
--     人工/CSV 导入的每一批都留痕：谁、什么时候、从哪个平台、进了几条。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_import_batch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key      TEXT    NOT NULL,
    source_name     TEXT,
    items_in        INTEGER NOT NULL DEFAULT 0,
    items_saved     INTEGER NOT NULL DEFAULT 0,
    items_skipped   INTEGER NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
