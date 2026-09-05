import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import InstrumentOptionsEvidence from '@/components/InstrumentOptionsEvidence.vue'
import type { OptionsData } from '@/types/api'

const options: OptionsData = {
  is_mock: false,
  status: 'available',
  available: true,
  data_state: 'live',
  provider: 'test-provider',
  source_mode: 'snapshot',
  captured_at: '2026-08-05T05:30:00Z',
  requested_symbols: ['INTC'],
  unavailable_symbols: [],
  symbols: [{
    symbol: 'INTC',
    spot: 100.86,
    spot_time: '2026-08-05T05:30:00Z',
    overview: {},
    expirations: [{
      expiration: '2026-08-05',
      days_to_expiry: 1,
      contract_count: 6,
      max_pain: 91,
      expected_move: { amount: 5.24, percent: 5.2, atm_strike: 100 },
      exposure: {
        totals: {
          call_dex: 56_891_700,
          put_dex: -3_217_800,
          net_dex: 26_300_000,
          absolute_dex: 60_109_500,
          call_gex: 0,
          put_gex: 0,
          modeled_net_gex: 22_634_500,
          absolute_gex: 22_634_500,
        },
        walls: {
          call_dex: { strike: 95, exposure: 56_891_700 },
          put_dex: { strike: 90, exposure: -3_217_800 },
          net_dex: { strike: 95, exposure: 56_666_000 },
        },
        by_strike: [],
        gamma_zones: [],
        gamma_noise_threshold: 0,
        usable_delta_contracts: 6,
        usable_gamma_contracts: 6,
      },
    }],
  }],
  subscription_quota: {},
  model_assumptions: [],
  warnings: [],
  note: '',
}

describe('InstrumentOptionsEvidence', () => {
  it('labels option spot fallback honestly and exposes expected-move bounds', () => {
    const wrapper = mount(InstrumentOptionsEvidence, {
      props: { options, alignment: null, symbol: 'INTC' },
    })

    const priceCard = wrapper.find('.instrument-options-decision-price')
    expect(priceCard.find('span').text()).toBe('OPTION SPOT')
    expect(priceCard.find('small').text()).toContain('官方收盘价缺失')
    expect(wrapper.find('.instrument-options-alignment-status').text()).toBe('无回看结果')
    expect(wrapper.findAll('.instrument-options-decision-detail')[0].find('small').text()).toBe('OPTION SPOT 距离 5.81%')
    expect(wrapper.find('.instrument-options-level-map-head').text()).toContain('option spot reference · close unavailable')
    expect(wrapper.find('.instrument-options-expected-band-title').text()).toBe('EXPECTED MOVE · 95.62 — 106.10')
    expect(wrapper.find('.instrument-options-level-legend').text()).toContain('OPTION SPOT 参考价')
    expect(wrapper.find('.instrument-options-support strong').text()).toBe('95.62 — 106.10')
    expect(wrapper.findAll('.instrument-options-level-axis span')).toHaveLength(2)
  })

  it('collapses a real no-hit structure into a compact summary until expanded', async () => {
    const noHitAlignment = {
      available: true,
      status: 'clear',
      source_phase: 'post_close_observation',
      method: 'regular_close_vs_option_levels',
      proximity_percent: 0.6,
      price_definition: 'regular close',
      causality_note: 'fixture',
      symbols: [{
        symbol: 'INTC',
        status: 'clear',
        close_price: 100.86,
        close_time: '2026-08-05T05:30:00Z',
        price_source: 'fixture',
        spot: 100.86,
        flags: [],
        flagged: false,
        expirations: [{
          expiration: '2026-08-05',
          max_pain: 91,
          max_pain_distance: 9.86,
          max_pain_distance_percent: 9.79,
          near_max_pain: false,
          dex_walls: [
            { kind: 'call_dex', label: 'CALL DEX', strike: 95, exposure: 1, distance: 5.86, distance_percent: 5.81, near: false },
            { kind: 'put_dex', label: 'PUT DEX', strike: 90, exposure: -1, distance: 10.86, distance_percent: 10.77, near: false },
            { kind: 'net_dex', label: 'NET DEX', strike: 95, exposure: 1, distance: 5.86, distance_percent: 5.81, near: false },
          ],
          near_dex_wall: false,
          dex_influence_candidate: false,
          flags: [],
        }],
      }],
      flagged_symbols: [],
      flag_count: 0,
      unavailable_symbols: [],
      warnings: [],
    } as never
    const wrapper = mount(InstrumentOptionsEvidence, {
      props: { options, alignment: noHitAlignment, symbol: 'INTC' },
    })

    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(true)
    expect(wrapper.find('.instrument-options-compact-summary').text()).toContain('未命中')
    expect(wrapper.find('.instrument-options-compact-summary').text()).toContain('CALL DEX / NET DEX · 95.00')
    expect(wrapper.find('.instrument-options-compact-summary').text()).toContain('2026-08-05')
    expect(wrapper.find('.instrument-options-level-map').exists()).toBe(false)

    await wrapper.get('.instrument-options-expand').trigger('click')
    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(false)
    expect(wrapper.find('.instrument-options-level-map').exists()).toBe(true)

    await wrapper.get('.instrument-options-collapse').trigger('click')
    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(true)
  })

  it('resets the no-hit collapse state when the symbol or expiration changes', async () => {
    const clearExpiry = (expiration: string) => ({
      expiration,
      max_pain: 91,
      max_pain_distance: 9.86,
      max_pain_distance_percent: 9.79,
      near_max_pain: false,
      dex_walls: [
        { kind: 'call_dex', label: 'CALL DEX', strike: 95, exposure: 1, distance: 5.86, distance_percent: 5.81, near: false },
        { kind: 'put_dex', label: 'PUT DEX', strike: 90, exposure: -1, distance: 10.86, distance_percent: 10.77, near: false },
        { kind: 'net_dex', label: 'NET DEX', strike: 95, exposure: 1, distance: 5.86, distance_percent: 5.81, near: false },
      ],
      near_dex_wall: false,
      dex_influence_candidate: false,
      flags: [],
    })
    const intcExpirations = [options.symbols[0].expirations[0], { ...options.symbols[0].expirations[0], expiration: '2026-09-19', days_to_expiry: 45 }]
    const multiOptions = {
      ...options,
      symbols: [
        { ...options.symbols[0], expirations: intcExpirations },
        { ...options.symbols[0], symbol: 'AMD', expirations: [{ ...options.symbols[0].expirations[0], expiration: '2026-08-12' }] },
      ],
    }
    const clearAlignment = {
      available: true,
      status: 'clear',
      source_phase: 'post_close_observation',
      method: 'regular_close_vs_option_levels',
      proximity_percent: 0.6,
      price_definition: 'regular close',
      causality_note: 'fixture',
      symbols: [
        { symbol: 'INTC', status: 'clear', close_price: 100.86, close_time: '2026-08-05T05:30:00Z', price_source: 'fixture', spot: 100.86, flags: [], flagged: false, expirations: [clearExpiry('2026-08-05'), clearExpiry('2026-09-19')] },
        { symbol: 'AMD', status: 'clear', close_price: 100.86, close_time: '2026-08-05T05:30:00Z', price_source: 'fixture', spot: 100.86, flags: [], flagged: false, expirations: [clearExpiry('2026-08-12')] },
      ],
      flagged_symbols: [], flag_count: 0, unavailable_symbols: [], warnings: [],
    } as never
    const wrapper = mount(InstrumentOptionsEvidence, {
      props: { options: multiOptions, alignment: clearAlignment, symbol: 'INTC' },
    })

    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(true)
    await wrapper.get('.instrument-options-expand').trigger('click')
    await wrapper.findAll('.instrument-options-expirations button')[1].trigger('click')
    await nextTick()
    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(true)
    expect(wrapper.find('.instrument-options-level-map').exists()).toBe(false)

    await wrapper.get('.instrument-options-expand').trigger('click')
    await wrapper.setProps({ symbol: 'AMD' })
    await nextTick()
    expect(wrapper.find('.instrument-options-compact-summary').exists()).toBe(true)
    expect(wrapper.find('.instrument-options-level-map').exists()).toBe(false)
  })
})
