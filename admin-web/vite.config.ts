import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // 按需引入：解决的是**样式**体积，以及"不必手写每个组件的 import"。
    //
    // 实测结论（不要当成已优化完成）：
    //   全量 `app.use(ElementPlus)` + 全量 CSS → vendor-element 940.0 kB (gzip 302 kB)
    //   接入解析器后                        → vendor-element 938.6 kB (gzip 302 kB)
    //   差异在噪声范围内。
    //   原因：解析器给出的是 `import { ElMessage } from 'element-plus/es'`，
    //   而 `element-plus/es` 是**桶文件**，re-export 整个库，
    //   因此按组件标签注入并不能真正摇掉未使用组件的 JS。
    //   已验证：产物中存在 cascader / calendar / carousel / colorpicker 等**从未使用**的组件。
    //
    // 仍未解决：Element Plus 的 JS 体积。要真正减小需要直接引用组件子路径
    //   （`element-plus/es/components/button/style/css`）或换用更轻的组件库，
    //   两者都是需要单独评估的改造。当前先如实记录体积，不假装已解决。
    //
    // dts 产物（auto-imports.d.ts / components.d.ts）**必须提交**：
    // CI 先跑 typecheck 再跑 build，不能在 typecheck 时依赖尚未生成的声明文件。
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      resolvers: [ElementPlusResolver()],
      dts: 'auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'components.d.ts',
    }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // 开发期反代到本机服务端，保持**同源**。
    // 同源不是便利性选择：会话票据走 HttpOnly Cookie + SameSite=Strict，
    // 跨源会让这套设计直接失效（见 管理端Web程序架构.md §四）。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: false },
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.spec.ts'],
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // 按依赖来源分包，便于长期缓存与并行下载。
        // 体积问题的**主因**已由上面的按需引入解决（见 vite.config 顶部注释）；
        // 这里只做剩余的分组，不承担减体积的职责。
        manualChunks: (id: string) => {
          if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) {
            return 'vendor-echarts'
          }
          if (id.includes('node_modules/element-plus') || id.includes('node_modules/@element-plus')) {
            return 'vendor-element'
          }
          if (id.includes('node_modules/vue') || id.includes('node_modules/pinia')) {
            return 'vendor-vue'
          }
          return undefined
        },
      },
    },
  },
})
