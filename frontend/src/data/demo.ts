import type { DemoCase } from '../types'

// Illustrative product walkthrough only. Not BIRD questions, benchmark data, or actual SQL execution.
export const demoCases: DemoCase[] = [
  {
    id: 'demo-orders', name: '订单金额汇总', database: '演示 · 订单仓库', source: 'DEMO',
    question: '按城市统计已完成订单的总金额，按金额从高到低排列。',
    evidence: '演示口径：status = completed 表示已完成；amount 单位为元。',
    schema: 'orders\n  id INTEGER · PRIMARY KEY\n  customer_id INTEGER · FK → customers.id\n  amount NUMERIC\n  status TEXT\n\ncustomers\n  id INTEGER · PRIMARY KEY\n  city TEXT',
    sql: "SELECT c.city, SUM(o.amount) AS total_amount\nFROM orders AS o\nJOIN customers AS c ON c.id = o.customer_id\nWHERE o.status = 'completed'\nGROUP BY c.city\nORDER BY total_amount DESC;",
    columns: ['city', 'total_amount'], rows: [{ city: '上海', total_amount: 12800 }, { city: '杭州', total_amount: 9600 }, { city: '成都', total_amount: 7200 }], error: null,
  },
  {
    id: 'demo-catalog', name: '产品分类统计', database: '演示 · 产品目录', source: 'DEMO',
    question: '统计每个分类中在售产品的数量。', evidence: '演示口径：active = 1 表示在售；仅展示固定快照。',
    schema: 'products\n  id INTEGER · PRIMARY KEY\n  category TEXT\n  active INTEGER',
    sql: 'SELECT category, COUNT(*) AS product_count\nFROM products\nWHERE active = 1\nGROUP BY category;',
    columns: ['category', 'product_count'], rows: [{ category: '办公设备', product_count: 24 }, { category: '智能配件', product_count: 16 }], error: null,
  },
  {
    id: 'demo-error', name: '缺列错误与修复准入', database: '演示 · 产品目录', source: 'DEMO',
    question: '统计每个产品分类的数量。', evidence: '故意保留错误列名，演示结构性错误分类；不会发起 Repair 请求。',
    schema: 'products\n  id INTEGER · PRIMARY KEY\n  category TEXT\n  active INTEGER',
    sql: 'SELECT category_name, COUNT(*)\nFROM products\nGROUP BY category_name;', columns: [], rows: [],
    error: 'missing_column · no such column: category_name。满足结构性错误准入；本演示不请求修复模型。',
  },
]
