<template>
  <div class="page center">
    <el-result icon="warning" title="无权限" sub-title="当前账号没有访问该功能的权限。">
      <template #extra>
        <el-button type="primary" @click="$router.push('/overview')">返回总览</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.center { display: flex; align-items: center; justify-content: center; height: 100%; }
</style>
