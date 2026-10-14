package com.km3.console

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface {
                    // TODO 骨架占位：接入导航后替换为 登录 → 设备选择 → 控制/状态/数据/视频/配置/升级
                    // 页面划分见 docs/架构设计/前端程序架构.md §3.1
                    Text("km3 控制台（骨架）")
                }
            }
        }
    }
}
