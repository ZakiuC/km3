import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import type { Capability } from '@/permissions'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    { path: '/', redirect: '/overview' },
    {
      path: '/overview',
      name: 'overview',
      component: () => import('@/views/OverviewView.vue'),
      meta: { capability: 'view_device' },
    },
    {
      path: '/devices/:id/sensors',
      name: 'sensor-history',
      component: () => import('@/views/SensorHistoryView.vue'),
      meta: { capability: 'view_device' },
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('@/views/LogsView.vue'),
      meta: { capability: 'view_logs' },
    },
    {
      // B10 占位页。口径待定，不渲染臆造统计（管理端Web程序架构.md §七）。
      path: '/workhours',
      name: 'workhours',
      component: () => import('@/views/WorkHoursView.vue'),
      meta: { capability: 'view_logs' },
    },
    {
      path: '/users',
      name: 'users',
      component: () => import('@/views/UsersView.vue'),
      meta: { capability: 'manage_users' },
    },
    {
      path: '/ota',
      name: 'ota',
      component: () => import('@/views/OtaView.vue'),
      meta: { capability: 'upload_firmware' },
    },
    {
      path: '/forbidden',
      name: 'forbidden',
      component: () => import('@/views/ForbiddenView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
    },
  ],
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (!session.restored) await session.restore()

  if (to.meta.public === true) return true
  if (!session.user) return { name: 'login', query: { redirect: to.fullPath } }

  const capability = to.meta.capability as Capability | undefined
  if (capability && !session.has(capability)) return { name: 'forbidden' }
  return true
})

export default router
