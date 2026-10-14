import { describe, expect, it } from 'vitest'

import { parseInline, parseMarkdown } from '@/components/agent/markdown'

/** 助手正文 markdown 子集解析：行内标记、块类型、边界与安全（不产生 HTML）。 */

describe('parseInline', () => {
  it('解析粗体、斜体与行内代码', () => {
    expect(parseInline('**依据**')).toEqual([{ kind: 'strong', text: '依据' }])
    expect(parseInline('__依据__')).toEqual([{ kind: 'strong', text: '依据' }])
    expect(parseInline('*略*')).toEqual([{ kind: 'em', text: '略' }])
    expect(parseInline('_略_')).toEqual([{ kind: 'em', text: '略' }])
    expect(parseInline('`concept_code`')).toEqual([{ kind: 'code', text: 'concept_code' }])
  })

  it('保留标记之间的普通文本与顺序', () => {
    expect(parseInline('依据：**直接关系** 与 `#6`')).toEqual([
      { kind: 'text', text: '依据：' },
      { kind: 'strong', text: '直接关系' },
      { kind: 'text', text: ' 与 ' },
      { kind: 'code', text: '#6' },
    ])
  })

  it('行内代码内部的标记不再解释', () => {
    expect(parseInline('`a**b**c`')).toEqual([{ kind: 'code', text: 'a**b**c' }])
  })

  it('词内下划线不是强调（标识符不得被渲染成斜体）', () => {
    expect(parseInline('concept_code')).toEqual([{ kind: 'text', text: 'concept_code' }])
    expect(parseInline('space_id 与 `x`')).toEqual([
      { kind: 'text', text: 'space_id 与 ' },
      { kind: 'code', text: 'x' },
    ])
  })

  it('未闭合标记按纯文本保留（流式到达途中会短暂出现）', () => {
    expect(parseInline('**未闭合')).toEqual([{ kind: 'text', text: '**未闭合' }])
    expect(parseInline('结尾是 *')).toEqual([{ kind: 'text', text: '结尾是 *' }])
  })

  it('空字符串不产生 token', () => {
    expect(parseInline('')).toEqual([])
  })

  it('尖括号文本只是字符，不是标记', () => {
    expect(parseInline('<script>alert(1)</script>')).toEqual([
      { kind: 'text', text: '<script>alert(1)</script>' },
    ])
  })
})

describe('parseMarkdown 块结构', () => {
  it('单换行保留为段内换行，空行分段', () => {
    expect(parseMarkdown('第一行\n第二行')).toEqual([
      {
        kind: 'paragraph',
        lines: [[{ kind: 'text', text: '第一行' }], [{ kind: 'text', text: '第二行' }]],
      },
    ])
    expect(parseMarkdown('第一段\n\n第二段')).toHaveLength(2)
  })

  it('无序与有序列表各自成块，类型切换会拆块', () => {
    const blocks = parseMarkdown('- 甲\n- 乙\n1. 丙')
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ kind: 'list', ordered: false })
    expect(blocks[1]).toMatchObject({ kind: 'list', ordered: true })
    expect(blocks[0]).toMatchObject({ items: [[{ kind: 'text', text: '甲' }], [{ kind: 'text', text: '乙' }]] })
  })

  it('标题按层级解析，`#` 后无空格不算标题', () => {
    expect(parseMarkdown('## 依据')).toEqual([
      { kind: 'heading', level: 2, line: [{ kind: 'text', text: '依据' }] },
    ])
    expect(parseMarkdown('#hashtag')).toMatchObject([{ kind: 'paragraph' }])
  })

  it('管道表格：分隔行成立时才成块，且表格后的行不被吞掉', () => {
    const blocks = parseMarkdown(
      '| 来源层级 | 可用叫法 |\n|---|---|\n| 个人偏好 | 未设置 |\n| 系统标准 | 妻子 |\n\n后续段落',
    )
    expect(blocks.map((block) => block.kind)).toEqual(['table', 'paragraph'])
    expect(blocks[0]).toMatchObject({
      header: [[{ kind: 'text', text: '来源层级' }], [{ kind: 'text', text: '可用叫法' }]],
      rows: [
        [[{ kind: 'text', text: '个人偏好' }], [{ kind: 'text', text: '未设置' }]],
        [[{ kind: 'text', text: '系统标准' }], [{ kind: 'text', text: '妻子' }]],
      ],
    })
    expect(blocks[1]).toMatchObject({ kind: 'paragraph' })
  })

  it('没有分隔行的管道文本仍是普通段落（不误吞）', () => {
    expect(parseMarkdown('| 只是竖线 | 文本 |')).toMatchObject([{ kind: 'paragraph' }])
  })

  it('表格单元格内的行内标记正常解析', () => {
    const blocks = parseMarkdown('| a | b |\n|---|---|\n| **粗** | `x` |')
    expect(blocks[0]).toMatchObject({
      rows: [[[{ kind: 'strong', text: '粗' }], [{ kind: 'code', text: 'x' }]]],
    })
  })

  it('围栏代码保留原始内容且不解释标记', () => {
    expect(parseMarkdown('```\n**原样**\n```')).toEqual([{ kind: 'code', text: '**原样**' }])
    // 未闭合围栏按代码块收尾，不丢内容
    expect(parseMarkdown('```\n未闭合')).toEqual([{ kind: 'code', text: '未闭合' }])
  })

  it('现场形态：粗体小标题 + 列表 + 段落', () => {
    const blocks = parseMarkdown('**依据（已确认事实 + 派生路径）：**\n\n- 关系路径为直接路径\n- 以你为参照世代\n\n如需继续，随时告诉我。')
    expect(blocks.map((block) => block.kind)).toEqual(['paragraph', 'list', 'paragraph'])
    expect(blocks[0]).toMatchObject({ lines: [[{ kind: 'strong', text: '依据（已确认事实 + 派生路径）：' }]] })
    expect(blocks[1]).toMatchObject({
      kind: 'list',
      ordered: false,
      items: [
        [{ kind: 'text', text: '关系路径为直接路径' }],
        [{ kind: 'text', text: '以你为参照世代' }],
      ],
    })
    expect(blocks[2]).toMatchObject({ kind: 'paragraph' })
  })

  it('空输入不产生块', () => {
    expect(parseMarkdown('')).toEqual([])
  })
})
