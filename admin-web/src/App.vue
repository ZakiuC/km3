<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import type { Capability } from '@/permissions'
import EnvironmentBanner from '@/components/EnvironmentBanner.vue'

const session = useSessionStore()
const router = useRouter()

interface NavItem {
  to: string
  label: string
  capability: Capability
}

const NAV: NavItem[] = [
  { to: '/overview', label: '总览', capability: 'view_device' },
  { to: '/logs', label: '日志', capability: 'view_logs' },
  { to: '/workhours', label: '工时统计', capability: 'view_logs' },
  { to: '/users', label: '用户与邀请', capability: 'manage_users' },
  { to: '/ota', label: '固件与升级', capability: 'upload_firmware' },
]

// 菜单按能力过滤。这只是隐藏入口——绕过前端直接调 API 仍会被服务端拒绝。
const visibleNav = computed(() => NAV.filter((n) => session.has(n.capability)))

async function onLogout(): Promise<void> {
  await session.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="shell">
    <EnvironmentBanner />

    <div v-if="session.user" class="body">
      <aside class="side">
        <div class="brand">km3 管理后台</div>
        <el-menu :default-active="$route.path" router>
          <el-menu-item v-for="n in visibleNav" :key="n.to" :index="n.to">
            {{ n.label }}
          </el-menu-item>
        </el-menu>
      </aside>

      <main class="main">
        <header class="topbar">
          <span class="who">
            {{ session.user.username }}
            <el-tag size="small" type="info">{{ session.user.role }}</el-tag>
          </span>
          <el-button link type="primary" @click="onLogout">退出登录</el-button>
        </header>
        <router-view />
      </main>
    </div>

    <!-- 未登录时只渲染路由出口（登录页 / 错误页），不显示侧栏 -->
    <router-view v-else />
  </div>
</template>
