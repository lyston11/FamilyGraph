# Design — 系统管理员后台视觉重设计

采用“FamilyGraph Observatory”方向：以用户端星空家庭壳为底，加入更克制的运营控制台层级。顶部改为带品牌标记、上下文说明和账号胶囊的两段式结构；导航使用带序号/短标签的胶囊；主内容采用宽幅 editorial 标题区与不等宽指标卡；列表卡片保留表格可读性并用细线、状态色和悬浮层次区分信息。

复用现有 `--ag-*`/shared brand tokens、既有组件 class 与 testid。所有视觉改动集中在 AdminShell、OverviewView 和 main.css，不改变业务数据流。动效仅使用轻量入场/hover，尊重 reduced-motion。
