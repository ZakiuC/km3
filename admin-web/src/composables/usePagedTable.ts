import { ref, type Ref } from 'vue'

export interface PageQuery {
  page: number
  size: number
}

/**
 * 分页取值。列表接口统一支持 ?page=&size=（服务器端程序架构.md §3.1）。
 *
 * 集中一处的理由：每个页面各写一遍 loading / error / 页码边界，
 * 迟早会出现某个页面忘了重置页码、翻到第 3 页换了筛选条件后停在空白页。
 */
export function usePagedTable<T>(
  fetcher: (q: PageQuery) => Promise<{ items: T[]; total: number }>,
  size = 50,
) {
  const items: Ref<T[]> = ref([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const res = await fetcher({ page: page.value, size })
      items.value = res.items
      total.value = res.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败'
      items.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  /** 筛选条件变化时必须回到第 1 页，否则会停在超出范围的空白页。 */
  async function reload(): Promise<void> {
    page.value = 1
    await load()
  }

  return { items, total, page, size, loading, error, load, reload }
}
