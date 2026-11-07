/**
 * 窄屏判定：移动端布局的单一开关。
 *
 * 用 matchMedia 而不是监听 resize：浏览器只在跨越断点时触发一次，
 * 避免滚动地址栏显隐导致的事件风暴。SSR/无 window 环境下降级为 false。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const QUERY = '(max-width: 860px)'

export function useIsMobile() {
  const isMobile = ref(
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia(QUERY).matches
      : false
  )

  let mql = null
  const onChange = (e) => { isMobile.value = e.matches }

  onMounted(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    mql = window.matchMedia(QUERY)
    isMobile.value = mql.matches
    // Safari 14 以前只支持 addListener
    if (mql.addEventListener) mql.addEventListener('change', onChange)
    else mql.addListener(onChange)
  })

  onBeforeUnmount(() => {
    if (!mql) return
    if (mql.removeEventListener) mql.removeEventListener('change', onChange)
    else mql.removeListener(onChange)
  })

  return { isMobile }
}
