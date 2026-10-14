<template>
  <div class="page center">
    <el-result icon="info" title="页面不存在" sub-title="链接可能已失效，或该功能尚未开放。">
      <template #extra>
        <el-button type="primary" @click="$router.push('/overview')">返回总览</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.center { display: flex; align-items: center; justify-content: center; height: 100%; }
</style>
