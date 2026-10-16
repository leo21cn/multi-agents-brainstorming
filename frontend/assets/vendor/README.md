# 第三方单文件

本目录只放以单文件形式纳入的第三方前端库及其出处记录，不放自研代码。前端为免构建静态页，不经 npm、不引入打包器与依赖树；纳入的库须固定版本、记录来源与许可、保留校验值。

## markdown-it.min.js

| 项 | 值 |
|----|----|
| 库名 | markdown-it |
| 版本 | 14.1.0 |
| 许可 | MIT |
| 来源 | https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js |
| 交叉校验来源 | https://unpkg.com/markdown-it@14.1.0/dist/markdown-it.min.js |
| SHA-256 | `38c70a1e7ca91ab40e2d9e6e60129851a717ed1c7d4acbbdd41bf9503791cf68` |
| 字节数 | 123618 |
| 用途 | 房间界面（`/room/`）发言正文的 Markdown 渲染，由 `room/index.html` 在 `assets/api.js` 之前加载 |
| 全局名 | `window.markdownit` |

配置口径（在 `room/index.html` 构建实例时固定，不得放宽）：

- `html: false`：源文本中的尖括号一律转义为文字，输出标签全部由渲染器生成，故不需要额外的消毒器。
- `breaks: true`：单个换行渲染为 `<br>`，保住模型输出的行结构。
- `linkify: false`：裸地址保持文本，不自动成链。
- `typographer: false`：不改写引号与破折号。

管理界面与首页不加载本文件。房间页在 `window.markdownit` 不可用时回退为整体转义的纯文本呈现。

更新方式：以整文件替换，并同步更新本记录的版本、来源与 SHA-256。替换前从两个来源各取一次，二者 SHA-256 必须一致；文件首部的版本与许可横幅必须保留。
