<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { usersApi, type Invitation } from '@/api/users'
import { usePagedTable } from '@/composables/usePagedTable'
import type { Role, SessionUser } from '@/api/types'
import ErrorState from '@/components/ErrorState.vue'

const ROLES: Role[] = ['superadmin', 'admin', 'operator', 'viewer']

const { items, total, page, size, loading, error, load } = usePagedTable<SessionUser>(
  (q) => usersApi.list({ page: q.page, size: q.size }),
)

const inviteEmail = ref('')
const inviteRole = ref<Role>('operator')
const inviteLink = ref('')
const invitations = ref<Invitation[]>([])

async function onInvite(): Promise<void> {
  if (!inviteEmail.value) {
    ElMessage.warning('请填写邮箱')
    return
  }
  try {
    const inv = await usersApi.invite(inviteEmail.value, inviteRole.value)
    // 第一版不接邮件服务：由管理员转达邀请链接（服务器端程序架构.md §4.3）。
    inviteLink.value = `${location.origin}/invite?token=${inv.token}`
    ElMessage.success('邀请已生成，请把链接转达给被邀请人')
    inviteEmail.value = ''
    invitations.value = (await usersApi.invitations()).items
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '生成邀请失败')
  }
}

async function onChangeRole(id: number, role: Role): Promise<void> {
  try {
    await usersApi.updateRole(id, role)
    ElMessage.success('角色已更新')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '更新角色失败')
    await load() // 失败时回读服务端真值，避免界面停留在乐观假设上
  }
}

onMounted(async () => {
  await load()
  try {
    invitations.value = (await usersApi.invitations()).items
  } catch {
    // 邀请列表失败不影响用户列表可用。
  }
})
</script>

<template>
  <div class="page">
    <h3>用户与邀请</h3>

    <ErrorState v-if="error" :message="error" @retry="load" />

    <template v-else>
      <el-table v-loading="loading" :data="items">
        <el-table-column prop="username" label="用户名" />
        <el-table-column prop="email" label="邮箱" />
        <el-table-column label="角色" width="180">
          <template #default="{ row }">
            <el-select
              :model-value="row.role"
              size="small"
              @update:model-value="(r: Role) => onChangeRole(row.id, r)"
            >
              <el-option v-for="r in ROLES" :key="r" :label="r" :value="r" />
            </el-select>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="size"
        :current-page="page"
        @current-change="(p: number) => { page = p; load() }"
      />

      <h4>生成邀请</h4>
      <el-form inline>
        <el-form-item label="邮箱">
          <el-input v-model="inviteEmail" style="width: 240px" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="inviteRole" style="width: 160px">
            <el-option v-for="r in ROLES" :key="r" :label="r" :value="r" />
          </el-select>
        </el-form-item>
        <el-button type="primary" @click="onInvite">生成邀请链接</el-button>
      </el-form>

      <el-alert v-if="inviteLink" type="success" :closable="false">
        <template #title>
          <code>{{ inviteLink }}</code>
        </template>
        <p>该链接一次性使用，默认 72 小时过期。</p>
      </el-alert>

      <h4>待接受邀请</h4>
      <el-table :data="invitations" size="small">
        <el-table-column prop="email" label="邮箱" />
        <el-table-column prop="role" label="角色" width="140" />
        <el-table-column prop="expires_at" label="过期时间" width="210" />
      </el-table>
    </template>
  </div>
</template>

<style scoped>
.pager { margin-top: 12px; }
h4 { margin: 24px 0 12px; }
</style>
