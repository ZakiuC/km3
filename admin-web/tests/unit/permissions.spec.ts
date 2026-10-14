import { describe, expect, it } from 'vitest'
import { can, CAPABILITIES, type Capability } from '@/permissions'
import type { Role } from '@/api/types'

describe('权限矩阵', () => {
  it('viewer 只读：能看设备与日志，不能下发命令、不能管用户', () => {
    expect(can('viewer', 'view_device')).toBe(true)
    expect(can('viewer', 'view_logs')).toBe(true)
    expect(can('viewer', 'send_command')).toBe(false)
    expect(can('viewer', 'manage_users')).toBe(false)
    expect(can('viewer', 'upload_firmware')).toBe(false)
  })

  it('operator 可控制与受限配置，但不能管用户与固件', () => {
    expect(can('operator', 'send_command')).toBe(true)
    expect(can('operator', 'modify_config')).toBe(true)
    expect(can('operator', 'manage_users')).toBe(false)
    expect(can('operator', 'upload_firmware')).toBe(false)
  })

  it('admin 可管用户与固件，但不能切换环境', () => {
    expect(can('admin', 'manage_users')).toBe(true)
    expect(can('admin', 'upload_firmware')).toBe(true)
    expect(can('admin', 'view_audit')).toBe(true)
    // 跨环境切换是 superadmin 独有（服务器端程序架构.md §4.4）
    expect(can('admin', 'switch_environment')).toBe(false)
  })

  it('superadmin 拥有全部能力', () => {
    for (const capability of Object.keys(CAPABILITIES) as Capability[]) {
      expect(can('superadmin', capability)).toBe(true)
    }
  })

  it('每个能力至少有一个角色可用，不存在无人能用的死能力', () => {
    for (const [name, roles] of Object.entries(CAPABILITIES)) {
      expect(roles.length, `能力 ${name} 没有可用角色`).toBeGreaterThan(0)
    }
  })

  it('跨环境切换只有 superadmin 一个角色', () => {
    expect(CAPABILITIES.switch_environment).toEqual(['superadmin'])
  })

  it('角色判定与权限矩阵对全部角色组合一致', () => {
    const roles: Role[] = ['superadmin', 'admin', 'operator', 'viewer']
    for (const role of roles) {
      const allowed = new Set(CAPABILITIES.view_device as readonly Role[])
      expect(can(role, 'view_device')).toBe(allowed.has(role))
    }
  })
})
