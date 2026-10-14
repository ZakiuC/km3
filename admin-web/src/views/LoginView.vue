<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useEnvironmentStore } from '@/stores/environment'

const session = useSessionStore()
const env = useEnvironmentStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function onSubmit(): Promise<void> {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  loading.value = true
  try {
    await session.login(username.value, password.value)
    // 环境列表只有登录后才拿得到（接口需要鉴权）。
    await env.load()
    const redirect = route.query.redirect
    await router.push(typeof redirect === 'string' ? redirect : { name: 'overview' })
  } catch (e) {
    // 用服务端给的错误码分支，而不是猜 HTTP 状态：登录失败与限流是两回事。
    const msg =
      e instanceof ApiError && e.code === 'ERATE_LIMIT'
        ? '尝试过于频繁，请稍后再试'
        : '用户名或密码错误'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>km3 管理后台</h2>
      <el-form label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="用户名">
          <el-input v-model="username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            show-password
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
.login-card { width: 340px; }
h2 { margin: 0 0 16px; font-size: 18px; }
</style>
