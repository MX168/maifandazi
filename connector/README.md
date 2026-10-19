# 麦当劳 MCP 连接器配置

本项目的数据全部来自麦当劳官方 MCP Server，接入方式有两种。

## 方式一：通过 WorkBuddy 连接器面板启用（推荐）

1. 打开 WorkBuddy → 左下角「连接器」管理入口
2. 搜索「麦当劳」并启用
3. 按提示完成授权

启用后 WorkBuddy 会自动写入本地配置，**无需手动编辑 JSON**。

## 方式二：手动写入配置

配置位于 `~/.workbuddy/mcp.json`（**注意不是** `~/.workbuddy/.mcp.json`）。
把 `mcd-mcp.example.json` 中的 `mcpServers` 条目合并进已有的 `mcpServers` 对象
（不要覆盖其他 server）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer <YOUR_MCP_TOKEN>"
      }
    }
  }
}
```

## ⚠️ 安全提醒（重要）

**不要把真实的 `Authorization` 令牌提交到公开仓库。**

本项目参赛需提交 GitHub 公开仓库，因此：

- 仓库中只保留 `mcd-mcp.example.json`，令牌为占位符 `<YOUR_MCP_TOKEN>`
- 真实令牌只存在于本机 `~/.workbuddy/mcp.json`，该文件**不应**被提交
- 建议在项目根目录的 `.gitignore` 中加入 `mcp.json` 与 `*.token`
- 若不慎提交过令牌，请立即在麦当劳开放平台吊销并重新签发

## 验证是否接入成功

启用后，在对话中让助手列出可用工具，应能看到如下前缀的工具：

```
mcp__mcd-mcp__query-meals
mcp__mcd-mcp__query-store-coupons
mcp__mcd-mcp__list-nutrition-foods
mcp__mcd-mcp__calculate-price
...
```

看不到即为未接入。此时本项目**不会**编造菜单与价格，而是提示用户先完成接入。
