import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import LoginView from '../LoginView.vue'

describe('LoginView', () => {
  it('渲染空壳登录页', () => {
    const wrapper = mount(LoginView)

    expect(wrapper.find('h1').text()).toBe('FamilyGraph')
  })
})
