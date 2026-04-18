# 如何获取B站登录凭证

## 1. 一键获取 Cookie（最快）

1. 登录 [bilibili.com](https://www.bilibili.com)
2. 按 `F12` 打开开发者工具 → **Console** 标签
3. 粘贴以下代码并回车：

```javascript
// 一键提取所有需要的凭证
const c = document.cookie.split(';').reduce((o, s) => { const [k,v] = s.trim().split('='); o[k] = v; return o; }, {});
const r = {
  SESSDATA: c.SESSDATA || '❌ 未找到',
  DedeUserID: c.DedeUserID || '❌ 未找到',
  bili_jct: c.bili_jct || '❌ 未找到',
  refresh_token: localStorage.getItem('ac_time_value') || '❌ 未找到'
};
console.log(JSON.stringify(r, null, 2));
// 点击输出结果即可复制
```

4. 点击 Console 输出的 JSON 即可复制

> **为什么要 refresh_token?** 它是 localStorage 中的 `ac_time_value`，配置后程序可以自动续期 SESSDATA，几乎不再需要手动更新。

## 2. 手动获取（Application 面板）

1. 登录 [bilibili.com](https://www.bilibili.com)
2. `F12` → **Application** → Cookies → `https://www.bilibili.com`
3. 逐个复制以下字段：
   - `SESSDATA` — 最重要，登录凭证
   - `bili_jct` — 防 CSRF token
   - `DedeUserID` — 你的B站 UID
4. `F12` → **Application** → Local Storage → `https://www.bilibili.com`
   - `ac_time_value` — 自动续期用

## 3. 本地配置

编辑 `config.yaml`：

```yaml
bilibili_uid: 12345678  # 你的UID
bilibili_credential:
  sessdata: "粘贴SESSDATA"
  bili_jct: "粘贴bili_jct"
  dedeuserid: "粘贴DedeUserID"
  refresh_token: "粘贴ac_time_value"  # 可选但强烈推荐
```

## 4. GitHub Actions 配置（一键部署）

### Step 1: Fork 仓库

点击右上角 **Fork** 按钮

### Step 2: 配置 Secrets

进入你 Fork 的仓库 → **Settings** → **Secrets and variables** → **Actions**

添加以下 Secrets：

| Name | 值 | 必需 |
|------|------|------|
| `SESSDATA` | Console 输出的 SESSDATA | ✅ |
| `DEDEUSERID` | 你的B站 UID | ✅ |
| `REFRESH_TOKEN` | Console 输出的 refresh_token | 推荐 |

> 💡 **强烈建议配置 REFRESH_TOKEN**，它可以自动续期 SESSDATA，避免每月手动更新。

### Step 3: 修改 config.yaml

根据需要修改：
- `favorite_ids`: 指定收藏夹ID
- `strategy`: 选取策略
- `notify.email`: 配置邮件推送

> ⚠️ **不要在 config.yaml 中填写 SESSDATA**，它由 GitHub Secrets 注入，写进 yaml 会被公开看到！

### Step 4: 验证运行

**Actions** 页面 → **Recall Dusty Favorites** → **Run workflow**

### 凭证过期怎么办？

程序会自动发送告警邮件到 `notify.email.receivers`，按邮件指引更新 Secrets 即可：

1. 登录 bilibili.com
2. F12 → Console → 粘贴一键脚本 → 复制新的 SESSDATA
3. Settings → Secrets → 更新 SESSDATA
4. 重新 Run workflow

## 5. 安全提醒

- Cookie 等同于账号密码，**绝不分享或上传到公开仓库**
- `config.yaml` 已在 `.gitignore` 中，不会被 git 跟踪
- GitHub Secrets 是加密存储的，只有 Actions 可以读取
- 配置 `refresh_token` 后 SESSDATA 会自动续期，大大减少手动操作
