package com.unison.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.unison.app.data.NotificationEntity

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(
    notifications: List<NotificationEntity>,
    isModelLoaded: Boolean,
    onOpenListenerSettings: () -> Unit,
    onClearAll: () -> Unit
) {
    var slothMode by remember { mutableStateOf(false) }
    var selectedPriority by remember { mutableStateOf("ALL") }

    val filteredList = remember(notifications, slothMode, selectedPriority) {
        notifications.filter { item ->
            if (slothMode && !item.actionRequired) false
            else if (selectedPriority != "ALL" && item.priority != selectedPriority) false
            else true
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("🦥", fontSize = 24.sp)
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text("Unison", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                            Text(
                                if (isModelLoaded) "On-Device AI Active" else "Fallback Rules Active",
                                fontSize = 11.sp,
                                color = if (isModelLoaded) Color(0xFF10B981) else Color(0xFFF59E0B)
                            )
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onOpenListenerSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Grant Notification Access")
                    }
                    IconButton(onClick = onClearAll) {
                        Icon(Icons.Default.Delete, contentDescription = "Clear All")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color(0xFF111827),
                    titleContentColor = Color(0xFFF1F5F9),
                    actionIconContentColor = Color(0xFF94A3B8)
                )
            )
        },
        containerColor = Color(0xFF0A0E17)
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(horizontal = 16.dp)
        ) {
            Spacer(modifier = Modifier.height(12.dp))

            // Sloth Mode Switch Card
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF182234)),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { slothMode = !slothMode }
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text("🦥 Sloth Mode", fontWeight = FontWeight.Bold, color = Color.White)
                        Text("Actionable notifications only", fontSize = 12.sp, color = Color(0xFF94A3B8))
                    }
                    Switch(
                        checked = slothMode,
                        onCheckedChange = { slothMode = it },
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = Color.White,
                            checkedTrackColor = Color(0xFF38BDF8)
                        )
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Filter Chips
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf("ALL", "High", "Low", "Noise").forEach { priority ->
                    val isSelected = selectedPriority == priority
                    FilterChip(
                        selected = isSelected,
                        onClick = { selectedPriority = priority },
                        label = { Text(if (priority == "ALL") "All Alerts" else priority) },
                        colors = FilterChipDefaults.filterChipColors(
                            containerColor = Color(0xFF182234),
                            labelColor = Color(0xFF94A3B8),
                            selectedContainerColor = Color(0xFF2563EB),
                            selectedLabelColor = Color.White
                        )
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Notifications Feed
            if (filteredList.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Default.Notifications,
                            contentDescription = null,
                            tint = Color(0xFF475569),
                            modifier = Modifier.size(48.dp)
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text("No notifications to display", color = Color(0xFF94A3B8), fontSize = 14.sp)
                    }
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.fillMaxSize()
                ) {
                    items(filteredList, key = { it.id }) { item ->
                        NotificationItemCard(item)
                    }
                }
            }
        }
    }
}

@Composable
fun NotificationItemCard(item: NotificationEntity) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFF182234)),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    val (badgeBg, badgeFg) = when (item.priority) {
                        "High" -> Pair(Color(0x33EF4444), Color(0xFFF87171))
                        "Low" -> Pair(Color(0x33F59E0B), Color(0xFFFBBF24))
                        else -> Pair(Color(0x3364748B), Color(0xFF94A3B8))
                    }
                    BadgeTag(text = item.priority, bg = badgeBg, fg = badgeFg)
                    BadgeTag(text = item.category, bg = Color(0x3364748B), fg = Color(0xFF94A3B8))

                    if (item.actionRequired) {
                        BadgeTag(text = "⚡ Action", bg = Color(0x3310B981), fg = Color(0xFF34D399))
                    }
                }
                Text(item.source, fontSize = 12.sp, color = Color(0xFFE2E8F0), fontWeight = FontWeight.SemiBold)
            }

            Spacer(modifier = Modifier.height(8.dp))
            Text(item.summary.ifBlank { item.content }, color = Color.White, fontSize = 14.sp)

            if (!item.suggestedReply.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(8.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFF0F172A), RoundedCornerShape(6.dp))
                        .padding(8.dp)
                ) {
                    Column {
                        Text("Suggested Reply:", fontSize = 11.sp, color = Color(0xFF38BDF8), fontWeight = FontWeight.Bold)
                        Text("\"${item.suggestedReply}\"", fontSize = 13.sp, color = Color(0xFFCBD5E1))
                    }
                }
            }
        }
    }
}

@Composable
fun BadgeTag(text: String, bg: Color, fg: Color) {
    Box(
        modifier = Modifier
            .background(bg, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(text.uppercase(), fontSize = 10.sp, color = fg, fontWeight = FontWeight.Bold)
    }
}

