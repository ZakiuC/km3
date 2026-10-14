<script setup lang="ts">
</script>

<!--
  B10 工时统计 —— **占位页，刻意不实现**。

  用户已明确指示：工时统计与分类需要工程后期才能确定具体形态。
  因此这里不渲染任何臆造的曲线或统计数字。

  理由见 管理端Web程序架构.md §七：服务器端文档 §4.9 已写明该功能
  "当前仅为提案"，分类来源（climb/inspect/idle/maintenance）尚未确认，
  表结构也缺少操作员维度。先做一版会产出**看起来能用但口径错误**的统计，
  而被引用的错误数据比没有数据更危险。

  定口径后的落点：服务端补 user_id 维度与聚合，本页替换为真实图表，
  路由与菜单无需改动。
-->
<template>
  <div class="page">
    <h3>工时统计</h3>
    <el-alert type="info" :closable="false" show-icon title="口径待定，尚未实现">
      <p>本功能（B10）的统计口径需要与业务方确认后才能实现。当前不做任何臆造的聚合。</p>
      <p>待确认项：</p>
      <ul>
        <li>分类判定来源（爬升 / 巡检 / 空闲 / 维护）由谁写入、依据是什么</li>
        <li>是否需要按操作员维度统计（当前表结构只有设备与日期）</li>
        <li>空闲时长如何界定"在线但无作业动作"</li>
      </ul>
      <p>
        接口签名已在 <code>src/api/analytics.ts</code> 预留，
        设计依据见 <code>docs/架构设计/管理端Web程序架构.md</code> §七。
      </p>
    </el-alert>
  </div>
</template>
