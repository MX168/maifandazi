# 麦当劳 MCP 接口速查（麦饭搭子专用）

所有工具名前缀为 `mcp__mcd-mcp__`。调用前需 ToolSearch 加载 schema，再用 DeferExecuteTool 执行。

## 一、beType / orderType / beCode 对照表（最容易出错的地方）

| 用餐场景 | beType | orderType | beCode | 门店来源 |
| --- | --- | --- | --- | --- |
| 到店自提 | `1` | `1` | **不传**（传了会报错） | `query-nearby-stores(beType=1)` |
| 得来速 Drive Through | `5` | `1` | **必传** | `query-nearby-stores(beType=5)` 返回里的 beCode |
| 麦乐送到家 | `2` | `2` | **必传** | `delivery-query-stores(beType=2, addressId)` |
| 企业团餐 | `6` | `2` | **必传** | `delivery-query-stores(beType=6, addressId)` |

规则记忆法：**orderType = 到店 1 / 外送 2**；**只有到店自提不传 beCode**。

## 二、标准调用链路

### 到店（自提 / 得来速）
1. `query-nearby-stores(beType=1|5, searchType=2, city=城市, keyword=地标)` → 拿到 `storeCode`（beType=5 时还有 `beCode`）
2. `query-meals(storeCode, orderType, beType[, beCode])` → 菜单与价格
3. `query-store-coupons(storeCode, orderType, beType[, beCode])` → **当前门店可用券**
4. `query-meal-detail(..., code)` → 需要看套餐构成或特调时用
5. `calculate-price(storeCode, orderType, beType, items:[...][, beCode])` → 实付金额
6. `create-order(storeCode, orderType, beType, items, takeWayCode, ...)` → 下单

### 麦乐送 / 企业团餐
1. `delivery-query-addresses()` → 地址列表，取 `addressId`
2. `delivery-query-stores(beType=2|6, addressId)` → `storeCode` + `beCode`
3. 同上的 2~6 步（团餐额外需要 `query-meal-assistance` 拿 `gmServiceCode`，以及 `query-promotions` 读满减规则）

## 三、关键字段与坑点

- **金额单位全部是「分」**：`calculate-price` 返回值除以 100 才是元。向用户展示前务必换算。
- **`create-order` 的 `takeWayCode`**：orderType=1 时必传，取值来自 `calculate-price` 返回的 `takeWayList[].code`，不能自己编。
- **特调 modulation 的 key 规则**（极易出错）：
  - 用户**选中**的特调项：key = `query-meal-detail` 返回的 `selectedKey`
  - 用户**未选中**但该项存在 `unselectedKey`：也必须传入，key 用 `unselectedKey`
  - 即：一个含有 unselectedKey 的特调组里，**全组成员都要传**
- **reservationDate**：只有预约场景才传，格式为 `yyyy-MM-dd HH:mm`；非预约场景传了可能报错。
- **storeCode 必须是纯数字**。
- `query-meal-detail` 的展示规则：`supportModify=true` 要在名称后标注【可特调】，但不要主动展开特调列表，用户问了再给。

## 四、三个「券」接口的区别（别调用错）

| 工具 | 含义 | 什么时候用 |
| --- | --- | --- |
| `available-coupons` | 当前**可领取**的麦麦省券 | 用户问「有什么券可以领」 |
| `query-my-coupons` | 用户**卡包里已有**的券（不做门店/渠道校验） | 用户问「我有什么券」 |
| `query-store-coupons` | **当前门店 + 当前订单类型**下确实可用的券 | **搭配方案算价前必用这个** |

## 五、企业团餐专属

- `query-promotions(storeCode, orderType=2, beType=6, beCode)` → 促销规则，`promotionType`：31=满额减，33=订单折扣；`ruleCategory`：30=满折，40=满减。工具**不做最优推荐**，需要自行按规则解读。
- `query-meal-assistance(storeCode, ...)` → `gmServiceCode`，在 `calculate-price` 时传回。
- 团餐搭配预算阶梯（官方建议）：

| 人均预算 | 推荐搭配 |
| --- | --- |
| 20 元以下 | 小食 |
| 20-30 元 | 汉堡 + 小食 或 汉堡 + 饮料 |
| 30-40 元 | 汉堡 + 薯条/小食 + 饮料 |
| 40-50 元 | 汉堡 + 薯条 + 小食 + 饮料 |
| 50 元以上 | 丰富组合，优先不重复小食 |

## 六、其他可用工具

| 工具 | 用途 |
| --- | --- |
| `list-nutrition-foods` | 全量餐品营养成分（能量/蛋白/脂肪/碳水/钠/钙），热量测算必用。**返回是纯文本表格，不是 JSON，必须先用 `parse_nutrition.py` 转换** |
| `now-time-info` | 取服务器当前时间，用于判断活动/营业时间 |
| `order-list` / `query-order` | 查历史订单 / 订单详情 |
| `cancel-order` | 取消订单 |
| `query-my-account` | 积分余额 |
| `query-lottery-info` / `draw-lottery` | 抽奖。**严禁自动抽**：必须先展示 `drawDecision.nextConsumption`，用户明确确认后才调用 `draw-lottery` |
| `campaign-calendar` | 活动日历 |

## 七、营养数据特殊处理（必读）

`list-nutrition-foods` 的 `data` 字段是**字符串**，不是 JSON 数组，实际形态：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  板烧鸡腿堡,null,1638,391,23,17,35,1041,93
```

处理办法：把 `data` 整段存成文本文件，交给解析脚本转成 planner 需要的 JSON：

```bash
python scripts/parse_nutrition.py --input nutrition_raw.txt --out nutrition.json
```

要点：

- 列名是 `productName` / `energyKcal` / `fat` / `protein` / `carbohydrate` / `sodium` / `calcium`，与 planner 内部使用的 `name` / `energy_kcal` / `fat_g` 等**不一致**，必须转换后再喂给 planner。
- `nutritionDescription` 是通俗类比文案（如「能量约清水1杯」「能量约猕猴桃2个」），多数为 `null`，非 null 时展示给用户很有意思。
- 接口里有重复条目（如「纯牛奶（盒装）」「小杯玉米杯」），解析脚本会自动去重。

## 八、异常处理

- 接口超时/失效：捕获异常，提示「当前菜单加载中，请稍后重试」，并给出经典套餐备选（如板烧鸡腿堡套餐 + 薯条 + 可乐）。
- 接口返回空菜单：提示更换门店或稍后重试，不要编造价格。
- **所有价格必须以 `calculate-price` 的返回为准**，不要拿 `query-meals` 的原始价直接算实付。
