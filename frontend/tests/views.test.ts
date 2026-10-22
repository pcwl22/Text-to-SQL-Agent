import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { ElButton, ElSelect, ElOption, ElInput, ElSwitch, ElTable, ElTableColumn, ElIcon } from 'element-plus'
import { routes } from '../src/router'
import { useObservatory } from '../src/stores/observatory'
import Playground from '../src/views/PlaygroundView.vue'
import Workflow from '../src/views/WorkflowView.vue'
import Experiments from '../src/views/ExperimentsView.vue'
import App from '../src/App.vue'

vi.mock('../src/components/ComparisonChart.vue', () => ({ default: { template: '<div data-chart="true">Chart</div>' } }))
vi.mock('@vue-flow/core', async importOriginal => {
  const actual = await importOriginal<typeof import('@vue-flow/core')>()
  return { ...actual, VueFlow: { props: ['nodes', 'edges'], emits: ['node-click'], template: '<div><button v-for="node in nodes" :key="node.id" @click="$emit(\'node-click\', { node })">{{ node.label }}</button></div>' } }
})
const components = { ElButton, ElSelect, ElOption, ElInput, ElSwitch, ElTable, ElTableColumn, ElIcon }
function context() {
  const pinia = createPinia(); setActivePinia(pinia)
  return { plugins: [pinia, createRouter({ history: createMemoryHistory(), routes })], components }
}
beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.useRealTimers() })

describe('offline replay interaction', () => {
  it('replays exactly the frozen snapshot with zero network', async () => {
    vi.useFakeTimers(); const wrapper = mount(Playground, { global: context() })
    expect(wrapper.text()).toContain('DEMO')
    await wrapper.findAll('button').find(b => b.text().includes('回放离线案例'))!.trigger('click')
    await vi.advanceTimersByTimeAsync(1500)
    expect(wrapper.text()).toContain('total_amount'); expect(wrapper.text()).toContain('12,800'.replace(',', ''))
    expect(useObservatory().phase).toBe('done'); expect(fetch).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('blocks arbitrary question changes and can restore frozen input', async () => {
    const wrapper = mount(Playground, { global: context() })
    await wrapper.find('textarea').setValue('DELETE FROM anything')
    const button = wrapper.findAll('button').find(b => b.text().includes('回放离线案例'))!
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('不会生成新 SQL')
    await wrapper.find('[aria-label="重置演示"]').trigger('click')
    expect(wrapper.find('textarea').element.value).toBe(useObservatory().demo.question)
    wrapper.unmount()
  })
  it('clears pending playback when leaving the view', async () => {
    vi.useFakeTimers(); const wrapper = mount(Playground, { global: context() })
    await wrapper.findAll('button').find(b => b.text().includes('回放离线案例'))!.trigger('click')
    const store = useObservatory(); wrapper.unmount()
    await vi.advanceTimersByTimeAsync(3000)
    expect(store.phase).toBe('idle'); expect(store.step).toBe(0)
  })
  it('shows a structural error snapshot without executing repair', async () => {
    vi.useFakeTimers(); const wrapper = mount(Playground, { global: context() })
    useObservatory().selectCase('demo-error'); await flushPromises()
    await wrapper.findAll('button').find(b => b.text().includes('回放离线案例'))!.trigger('click')
    await vi.advanceTimersByTimeAsync(1500)
    expect(wrapper.text()).toContain('missing_column'); expect(fetch).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
describe('architecture and paired analysis', () => {
  it('clicks node details and switches to baseline without a repair route', async () => {
    const wrapper = mount(Workflow, { global: context() })
    await wrapper.findAll('button').find(b => b.text().startsWith('Optional Repair'))!.trigger('click')
    expect(wrapper.find('.node-detail').text()).toContain('repair_prompt')
    await wrapper.findAll('button').find(b => b.text() === 'A · Baseline')!.trigger('click')
    expect(useObservatory().mode).toBe('baseline')
    expect(wrapper.text()).toContain('Baseline 为直接函数调用')
    wrapper.unmount()
  })
  it('shows C/D as unrun and the only correctness delta as 1317', () => {
    const wrapper = mount(Experiments, { global: context() })
    expect(wrapper.findAll('.experiment-tile').slice(2).every(c => c.text().includes('未真实运行'))).toBe(true)
    const changed = wrapper.findAll('.changed-row')
    expect(changed).toHaveLength(1); expect(changed[0]!.text()).toContain('1317')
    expect(wrapper.text()).toContain('UNKNOWN'); wrapper.unmount()
  })
})
describe('router and shell', () => {
  it.each(['/', '/playground', '/workflow', '/experiments', '/audit', '/about'])('renders route %s', async path => {
    const global = context(); const router = global.plugins[1] as ReturnType<typeof createRouter>
    await router.push(path); await router.isReady()
    const wrapper = mount(App, { global }); await flushPromises()
    expect(wrapper.find('h1').exists()).toBe(true)
    expect(wrapper.text()).toContain('离线演示环境')
    expect(wrapper.find('.nav-item.active').attributes('href')).toBe(path)
    wrapper.unmount()
  })
  it('provides mobile navigation and working theme controls', async () => {
    const wrapper = mount(App, { global: context() }); await flushPromises()
    await wrapper.find('[aria-label="打开导航"]').trigger('click')
    expect(wrapper.find('.sidebar.open').exists()).toBe(true)
    await wrapper.find('[aria-label="切换浅色主题"]').trigger('click')
    expect(document.documentElement.dataset.theme).toBe('light')
    wrapper.unmount()
  })
})
