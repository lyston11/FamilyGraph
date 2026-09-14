# 6. 授权矩阵（visibility.py 单点实现）

> **[v2 取代]** 下表 full/summary/invisible 三级口径及第 3 列「直系自动 full」已被 §0.1 四级合同取代；AD-9 家族空间外披露开关的存储已迁至 disclosure_preferences（全局+逐空间 scope），开关类别扩至高敏感类。

| 资源 \ 主体 | 本人 | 同空间 active 成员 | 直系结构边对端（elder/younger/spouse active） | peer 对端 / clan 连通可达 | 其余 |
|---|---|---|---|---|---|
| 档案详情字段 | full | full | full | summary(name/称谓/世代) | invisible |
| 图节点+关系边 | full | full | full | 仅摘要节点 | 不返回 |
| 头像原图 | full | full | full | 占位图 | 占位图 |
| 附件元数据/下载 | full | full | full | invisible | invisible |
| 搜索命中 | — | — | 允许(full 详情) | 允许(摘要) | 不可命中 |
| 统计聚合 | — | — | 计入范围 | 计入范围 | 不计入 |
| join_request | 目标空间 owner 可见审批 | — | — | — | — |
| 空间邀请（invite） | — | active 成员可邀请；受邀人需接受 | — | — | — |
| 空间管理者申请 | 提交（identity_confirmed active member）与查看本人申请 | — | — | — | — |
| 管理者申请裁决 | platform_operator only（队列/approve/reject + audit；见 §0.7） | — | — | — | — |
| 管理 API | is_admin only + audit | — | — | — | — |

- IDOR 集成测试逐行覆盖矩阵（普通 JWT 直打 API 断言遮罩/invisible）。
- 文件下载走授权端点流式返回（禁止 nginx 直链 uploads 目录），响应头 `Content-Disposition` + `X-Content-Type-Options: nosniff`。

#
#
#
 
家
族
空
间
外
披
露
开
关
 
`
[
A
D
-
9
]
`
（
2
0
2
6
-
0
8
-
2
5
 
用
户
裁
定
）




-
 
适
用
对
象
：
非
同
空
间
且
无
直
系
结
构
边
的
家
族
可
达
者
（
p
e
e
r
 
对
端
、
远
房
）
。


-
 
必
要
字
段
始
终
可
见
：
名
字
、
称
谓
标
签
、
世
代
角
标
。


-
 
其
余
字
段
按
*
*
五
个
类
别
开
关
*
*
由
归
属
者
决
定
是
否
在
家
族
空
间
公
开
：
`
a
v
a
t
a
r
`
 
/
 
`
p
h
o
t
o
s
`
(
相
册
)
 
/
 
`
d
a
t
e
s
`
(
生
卒
)
 
/
 
`
b
i
o
`
 
/
 
`
a
t
t
a
c
h
m
e
n
t
s
`
(
链
接
附
件
)
，
存
储
于
 
`
u
s
e
r
s
.
c
l
a
n
_
d
i
s
c
l
o
s
u
r
e
_
j
s
o
n
`
，
*
*
默
认
全
部
不
公
开
*
*
。


-
 
开
关
修
改
权
 
=
 
该
档
案
的
 
D
5
 
编
辑
权
主
体
（
c
l
a
i
m
e
d
 
本
人
；
m
a
n
a
g
e
d
 
档
案
为
代
管
人
）
。
A
P
I
：
`
P
U
T
 
/
u
s
e
r
s
/
{
i
d
}
/
d
i
s
c
l
o
s
u
r
e
`
。


-
 
v
i
s
i
b
i
l
i
t
y
.
p
y
 
在
矩
阵
第
 
4
 
列
（
p
e
e
r
/
c
l
a
n
 
可
达
）
判
定
时
消
费
此
配
置
：
开
放
的
类
别
返
回
 
f
u
l
l
，
未
开
放
返
回
 
M
A
S
K
E
D
 
结
构
；
搜
索
与
统
计
口
径
一
致
。


-
 
直
系
结
构
边
对
端
不
受
开
关
限
制
（
Q
U
1
=
B
：
完
整
互
见
）
。