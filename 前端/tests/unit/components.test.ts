import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import PairCard from '../../src/components/PairCard.vue'
import TranscriptList from '../../src/components/TranscriptList.vue'
import type { Card } from '../../src/state/rounds'

describe('PairCard', () => {
  it('显示配对表单', () => {
    const wrapper = mount(PairCard, { props: { pending: false, retrySeconds: 0 } })
    expect(wrapper.find('#pair-code').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
  it('pending时禁用输入和按钮', () => {
    const wrapper = mount(PairCard, { props: { pending: true, retrySeconds: 0 } })
    expect(wrapper.find('#pair-code').attributes('disabled')).toBeDefined()
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })
  it('retrySeconds>0时禁用', () => {
    const wrapper = mount(PairCard, { props: { pending: false, retrySeconds: 5 } })
    expect(wrapper.find('#pair-code').attributes('disabled')).toBeDefined()
  })
  it('提交时emit pair事件', async () => {
    const wrapper = mount(PairCard, { props: { pending: false, retrySeconds: 0 } })
    await wrapper.find('#pair-code').setValue('TESTCODE')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('pair')).toBeTruthy()
    expect(wrapper.emitted('pair')![0]).toEqual(['TESTCODE'])
  })
  it('提交后清空输入', async () => {
    const wrapper = mount(PairCard, { props: { pending: false, retrySeconds: 0 } })
    await wrapper.find('#pair-code').setValue('SECRET')
    await wrapper.find('form').trigger('submit')
    expect((wrapper.find('#pair-code').element as HTMLInputElement).value).toBe('')
  })
  it('空输入时按钮禁用', () => {
    const wrapper = mount(PairCard, { props: { pending: false, retrySeconds: 0 } })
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })
})

describe('TranscriptList', () => {
  function card(overrides: Partial<Card> = {}): Card {
    return {
      id: '00112233-4455-4677-8899-aabbccddeeff',
      created: Date.now(), phase: 'completed', text: '测试终稿',
      revision: 1, discarded: false, note: '已完成', samples: 16000,
      ...overrides,
    }
  }
  it('无卡片显示空状态', () => {
    const wrapper = mount(TranscriptList, { props: { cards: [], activeId: '' } })
    expect(wrapper.find('.empty-state').exists()).toBe(true)
  })
  it('有卡片不显示空状态', () => {
    const wrapper = mount(TranscriptList, { props: { cards: [card()], activeId: '' } })
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })
  it('显示卡片数量', () => {
    const wrapper = mount(TranscriptList, {
      props: { cards: [card(), card({ id: 'second-id', text: '第二张' })], activeId: '' }
    })
    const articles = wrapper.findAll('.transcript-card')
    expect(articles.length).toBe(2)
  })
  it('完成卡片显示终稿文字', () => {
    const wrapper = mount(TranscriptList, { props: { cards: [card({ text: '你好世界' })], activeId: '' } })
    expect(wrapper.find('.transcript-text').text()).toBe('你好世界')
  })
  it('草稿标记为非终稿', () => {
    const wrapper = mount(TranscriptList, {
      props: { cards: [card({ phase: 'recording', text: '草稿' })], activeId: '00112233-4455-4677-8899-aabbccddeeff' }
    })
    expect(wrapper.find('.transcript-text.draft').exists()).toBe(true)
  })
  it('当前轮有高亮', () => {
    const id = '00112233-4455-4677-8899-aabbccddeeff'
    const wrapper = mount(TranscriptList, { props: { cards: [card()], activeId: id } })
    expect(wrapper.find('.transcript-card.current').exists()).toBe(true)
  })
  it('完成卡片有复制按钮', () => {
    const wrapper = mount(TranscriptList, { props: { cards: [card()], activeId: '' } })
    expect(wrapper.find('.card-footer .text-button').exists()).toBe(true)
  })
  it('失败卡片不显示复制按钮', () => {
    const wrapper = mount(TranscriptList, {
      props: { cards: [card({ phase: 'failed', text: '' })], activeId: '' }
    })
    expect(wrapper.find('.card-footer .text-button').exists()).toBe(false)
  })
  it('取消卡片显示note', () => {
    const wrapper = mount(TranscriptList, {
      props: { cards: [card({ phase: 'cancelled', text: '', note: '已取消' })], activeId: '' }
    })
    expect(wrapper.text()).toContain('已取消')
  })
  it('失败草稿标注', () => {
    const wrapper = mount(TranscriptList, {
      props: { cards: [card({ phase: 'failed', text: '未完成', note: '超时' })], activeId: '' }
    })
    expect(wrapper.text()).toContain('未完成草稿')
  })
})
