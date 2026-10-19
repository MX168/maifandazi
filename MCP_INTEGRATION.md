# MCP 接口对接说明

> 本文说明「麦饭搭子」如何对接麦当劳官方 MCP Server，包含完整调用链路、参数约定、
> 实际踩到的坑，以及 MCP 能力在项目中的具体应用。

---

## 一、概述

项目通过 WorkBuddy 的**麦当劳官方 MCP 连接器**获取实时数据，工具前缀为 `mcp__mcd-mcp__`。

**设计原则**：

1. **不硬编码任何菜单与价格** —— 所有餐品、价格、营养均来自接口实时拉取
2. **金额计算以接口为准** —— 搭配算法给出候选，最终实付以 `calculate-price` 返回为准
3. **不编造优惠** —— 接口返回空券就是空券，如实展示

---

## 二、beType / orderType / beCode 三元组（最容易出错）

麦当劳的四个取餐渠道由这三个参数共同决定，**组合错了会直接报错**：

| 用餐场景 | beType | orderType | beCode | 门店来源 |
| --- | --- | --- | --- | --- |
| 到店自提 | `1` | `1` | **不传**（传了报错） | `query-nearby-stores(beType=1)` |
| 得来速 Drive Through | `5` | `1` | **必传** | `query-nearby-stores(beType=5)` |
| 麦乐送到家 | `2` | `2` | **必传** | `delivery-query-stores(beType=2, addressId)` |
| 企业团餐 | `6` | `2` | **必传** | `delivery-query-stores(beType=6, addressId)` |

记忆法：**orderType = 到店 1 / 外送 2**；**只有到店自提不传 beCode**。

---

## 三、完整调用链路

### 3.1 到店场景（自提 / 得来速）

```
1. query-nearby-stores(beType=1|5, searchType=2, city, keyword)
   └─> storeCode（beType=5 时还有 beCode）
2. query-meals(storeCode, orderType, beType[, beCode])
   └─> 菜单：categories[] 分类 + meals{} 详情映射（name / currentPrice / originalPrice / tags）
3. query-store-coupons(storeCode, orderType, beType[, beCode])
   └─> 当前门店当前渠道可用券（title / couponId / couponCode / tradeDateTime / products）
4. list-nutrition-foods()
   └─> 全量餐品营养成分（纯文本表格，需解析，见第五节）
5. [可选] query-meal-detail(storeCode, ..., code)
   └─> 查看套餐构成 / 特调选项
6. calculate-price(storeCode, orderType, beType, items[], beCode)
   └─> 实付金额（单位：分）+ takeWayList[]
7. create-order(storeCode, orderType, beType, items[], takeWayCode, ...)
   └─> 下单，返回支付链接
```

### 3.2 外送场景（麦乐送 / 企业团餐）

```
1. delivery-query-addresses()            └─> addressId
2. delivery-query-stores(beType=2|6, addressId)  └─> storeCode + beCode
3. 同到店场景第 2~7 步
   团餐额外：
   - query-meal-assistance(storeCode, ...)  └─> gmServiceCode（calculate-price 时回传）
   - query-promotions(storeCode, orderType=2, beType=6, beCode)  └─> 满减满折规则
```

### 3.3 本项目实际执行的链路（演示案例）

以 README 中的演示为例，实际调用：

```
query-nearby-stores(beType=1, searchType=2, city="深圳", keyword="科技园")
  └─> 麦当劳深圳同方信息港餐厅  storeCode=1420762  (distance 403m, 07:00-23:00)
query-meals(storeCode="1420762", orderType=1, beType=1)
  └─> 15 个分类 / 100+ 餐品（含 currentPrice 与 originalPrice）
query-store-coupons(storeCode="1420762", orderType=1, beType=1)
  └─> data: []      ← 该店当日无可用券，如实展示券省 ¥0
list-nutrition-foods()
  └─> [160]{...} 纯文本表格，经 parse_nutrition.py 转成结构化 JSON
```

> 注意：`query-nearby-stores` 用 `searchType=1`（收藏餐厅）时，若用户无收藏会返回
> `code=600050 收藏餐厅列表为空`，**必须回退到 `searchType=2` 让用户提供城市 + 关键词**。

---

## 四、关键参数约定与坑点

### 4.1 金额单位全部是「分」

`calculate-price` 返回的价格字段单位为**分**，展示给用户前必须除以 100。

```python
payable_yuan = response["data"]["payable"] / 100
```

### 4.2 `create-order` 的 `takeWayCode` 不能编造

orderType=1（到店/得来速）时必传，取值**必须**来自 `calculate-price` 返回的
`takeWayList[].code`。外送/团餐场景不传。

### 4.3 特调 `modification` 的 key 规则（极易出错）

来自 `query-meal-detail` 的返回：

- 用户**选中**的特调项：`key = selectedKey`
- 用户**未选中**但该组存在 `unselectedKey` 的项：**也必须传入**，`key = unselectedKey`

即：一个含 `unselectedKey` 的特调组里，**全组成员都要传**，选中的用 `selectedKey`，未选中的用 `unselectedKey`。

### 4.4 `reservationDate` 只在预约场景传

格式 `yyyy-MM-dd HH:mm`。非预约场景传了可能报错。

### 4.5 `storeCode` 必须是纯数字

### 4.6 `query-meal-detail` 的展示规则

- `supportModify=true` → 名称后标注【可特调】
- 不主动展开特调选项列表，用户问了才展示

---

## 五、营养接口的特殊处理（本项目重点解决）

### 5.1 问题

`list-nutrition-foods` 的 `data` 字段是**字符串**，不是 JSON 数组：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  板烧鸡腿堡,null,1638,391,23,17,35,1041,93
```

且列名是 `productName` / `energyKcal` / `fat` / `protein` / `carbohydrate` / `sodium` / `calcium`，
与代码里惯用的 `name` / `energy_kcal` / `fat_g` **完全不一致**。
如果直接把返回值往算法里塞，所有餐品都会显示「缺少营养数据」。

### 5.2 解决方案

`scripts/parse_nutrition.py` 负责把这段文本转成结构化 JSON：

```bash
python scripts/parse_nutrition.py --input nutrition_raw.txt --out nutrition.json
```

处理细节：

- 自动识别 `[N]{列名...}` 表头行，后续按 CSV 逐行解析
- 字段重命名映射（`productName → name`、`energyKcal → energy_kcal` 等）
- `null` 值转 `None`，数值转 `float`
- **同名去重**（接口里存在重复条目，如「纯牛奶（盒装）」「小杯玉米杯」）
- 保留 `nutritionDescription` 通俗类比（如「能量约清水1杯」），非 null 时展示给用户

`planner.py` 内部还做了第二层兼容（`read_nutrition`），**两套命名都能吃**，防止调用方给错格式。

### 5.3 名称匹配

营养表里的品名与菜单品名不完全一致（如营养表「中薯条」vs 菜单「薯条」）。
`lookup_nutrition` 采用：精确匹配 → 双向包含匹配（取字符重合度最高的）。

---

## 六、三个「券」接口的区别

这是官方接口设计上容易混淆的地方：

| 工具 | 含义 | 本项目何时用 |
| --- | --- | --- |
| `available-coupons` | 当前**可领取**的麦麦省券 | 用户问「有什么券能领」 |
| `query-my-coupons` | 用户**卡包里已有**的券（不做门店/渠道校验，**不承诺当前订单可用**） | 用户问「我有什么券」 |
| `query-store-coupons` | **指定门店 + 订单类型**下确实可用的券 | **搭配算价必用这个** |

用错会导致「券在卡包里但实际上这家店用不了」的错误推荐。

---

## 七、企业团餐专属能力

| 工具 | 用途 |
| --- | --- |
| `query-promotions(storeCode, orderType=2, beType=6, beCode)` | 促销规则。`promotionType`：31=满额减，33=订单折扣；`ruleCategory`：30=满折，40=满减。**接口只返回原始规则，不做最优推荐**，需自行解读 |
| `query-meal-assistance(storeCode, ...)` | `gmServiceCode`，在 `calculate-price` 时回传 |

官方给出的团餐人均预算搭配阶梯，已写入 `references/mcp-api.md`：

| 人均预算 | 推荐搭配 |
| --- | --- |
| 20 元以下 | 小食 |
| 20-30 元 | 汉堡 + 小食 或 汉堡 + 饮料 |
| 30-40 元 | 汉堡 + 薯条/小食 + 饮料 |
| 40-50 元 | 汉堡 + 薯条 + 小食 + 饮料 |
| 50 元以上 | 丰富组合，优先不重复小食 |

---

## 八、MCP 能力在项目中的具体应用

| MCP 能力 | 对应功能 | 落地方式 |
| --- | --- | --- |
| `query-nearby-stores` / `delivery-query-stores` | 门店与渠道定位 | 按 beType 分流，见第二节 |
| `query-meals` | 实时菜单与价格 | 直接作为搭配算法的输入，含 `currentPrice` 与 `originalPrice` |
| `query-meal-detail` | 套餐构成 / 特调 | 用户追问时按需调用 |
| `query-store-coupons` | 优惠券自动匹配 | 券的门槛与减免额参与实付计算 |
| `list-nutrition-foods` | 热量智能测算 | 经 `parse_nutrition.py` 解析后，逐人标注热量与高热量预警 |
| `calculate-price` | 实付金额复核 | **权威金额来源**，单位分 |
| `create-order` | 一键下单 | 需用户确认，`takeWayCode` 取自 `calculate-price` |
| `now-time-info` | 营业 / 活动时间判断 | 避免推荐当前不供应的餐品 |
| `query-lottery-info` / `draw-lottery` | （预留）积分抽奖 | **严禁自动抽**：必须展示 `drawDecision.nextConsumption` 并获用户明确确认 |

---

## 九、数据流转图

```
                    ┌──────────────────────────┐
                    │  麦当劳 MCP Server (官方)  │
                    └────────────┬─────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
   query-meals            query-store-coupons      list-nutrition-foods
   (菜单+价格)              (门店可用券)              (纯文本营养表)
        │                        │                        │
        │                        │                parse_nutrition.py
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  忌口 / 偏好 / 避雷清单    │
                    │  (对话采集 + profile.py)  │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   planner.py 搭配算法     │
                    │  零超支硬约束 + 套餐比价   │
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
        calculate-price      fun.py          profile.py
        (实付复核,权威)     (人格/幸运/战绩)    (避雷清单落盘)
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  最终方案 + 一键下单清单   │
                    └──────────────────────────┘
```

---

## 十、错误处理策略

| 情况 | 处理 |
| --- | --- |
| 未连接 MCP | 引导用户开通连接器，**禁止编造菜单与价格** |
| 接口超时 / 报错 | 提示「当前菜单加载中，请稍后重试」，给经典套餐备选（不写具体金额） |
| `query-nearby-stores` 返回空收藏 | 自动改用 `searchType=2` 请用户提供城市 + 关键词 |
| `query-store-coupons` 返回空数组 | 券省显示 ¥0，**不编造优惠** |
| `calculate-price` 与算法估算不一致 | **以 `calculate-price` 为准**，同步修正展示 |
| 预算不足 | 明确返回缺口金额与最低可行方案，交由用户决策 |

---

## 十一、为什么金额计算必须走脚本

搭配算法不交给大模型口算，而是由 `planner.py` 确定性执行，原因有三：

1. **零超支必须可证明** —— 约束写在评分函数里，超预算方案直接返回 `-∞`，不可能被语言模型「算错」放过
2. **组合空间大** —— 上百个 SKU 在预算内求较优解，需要搜索而非直觉
3. **可复现** —— 同一份输入必得同一份输出，便于测试与评审验证（`examples/` 下即为可复现样例）

大模型负责的是**理解需求、调用接口、组织表达**；
金额与组合的硬约束交给确定性代码 —— 这是本项目最核心的工程决策。

---

## 十二、接口清单

| 工具 | 项目中是否使用 |
| --- | --- |
| `query-nearby-stores` | ✅ 到店门店查询 |
| `delivery-query-addresses` | ✅ 外送地址 |
| `delivery-query-stores` | ✅ 外送门店 |
| `query-meals` | ✅ 菜单与价格 |
| `query-meal-detail` | ✅ 套餐构成 / 特调 |
| `query-store-coupons` | ✅ 门店可用券 |
| `query-my-coupons` | ✅ 卡包券查询 |
| `available-coupons` | ✅ 可领券 |
| `query-promotions` | ✅ 团餐促销 |
| `query-meal-assistance` | ✅ 团餐助餐服务 |
| `calculate-price` | ✅ 价格计算（权威） |
| `create-order` | ✅ 下单 |
| `list-nutrition-foods` | ✅ 热量测算 |
| `now-time-info` | ✅ 时间判断 |
| `order-list` / `query-order` / `cancel-order` | ⭕ 预留 |
| `query-my-account` | ⭕ 预留 |
| `query-lottery-info` / `draw-lottery` | ⭕ 预留（严禁自动抽） |
| `campaign-calendar` | ⭕ 预留 |

---

## 十三、接入配置与凭证安全

### 13.1 两种接入方式

**方式 A：通过 WorkBuddy 连接器面板启用（推荐）**

打开 WorkBuddy → 左下角「连接器」管理入口 → 搜索「麦当劳」并启用 → 按提示完成授权。
启用后 WorkBuddy 会自动写入本地配置，**无需手动编辑 JSON**。

**方式 B：手动写入配置**

配置文件位于 `~/.workbuddy/mcp.json`（**注意不是** `~/.workbuddy/.mcp.json`）。
把仓库根目录 [`mcp-config.example.json`](./mcp-config.example.json) 中的 `mcpServers`
条目**合并**进已有的 `mcpServers` 对象，不要覆盖其他 server。

### 13.2 凭证安全（重要）

**不要把真实的 `Authorization` 令牌提交到公开仓库。**

本项目参赛需提交 GitHub 公开仓库，因此：

- 仓库中只保留 `mcp-config.example.json`，令牌位置为环境变量占位符 `${MCD_MCP_TOKEN}`
- 真实令牌只存在于本机 `~/.workbuddy/mcp.json`，该文件**不应**被提交
- 建议在项目根目录的 `.gitignore` 中加入 `mcp.json` 与 `*.token`
- 若不慎提交过令牌，请立即在麦当劳开放平台吊销并重新签发

### 13.3 验证是否接入成功

启用后在对话中让助手列出可用工具，应能看到 `mcp__mcd-mcp__` 前缀的工具
（`query-meals`、`query-store-coupons`、`list-nutrition-foods`、`calculate-price` 等）。
看不到即为未接入。此时本项目**不会**编造菜单与价格，而是提示用户先完成接入。
