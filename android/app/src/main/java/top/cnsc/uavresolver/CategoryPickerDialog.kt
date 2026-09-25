package top.cnsc.uavresolver

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Divider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

sealed class PickerEntry {
    data class Header(val text: String) : PickerEntry()
    data class Item(val category: BrowseCategory) : PickerEntry()
}

@Composable
fun CategoryPickerDialog(
    entries: List<PickerEntry>,
    loading: Boolean,
    error: String?,
    onSelect: (BrowseCategory) -> Unit,
    onDismiss: () -> Unit,
) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier
                .fillMaxWidth(0.92f)
                .heightIn(max = 520.dp),
        ) {
            when {
                loading -> Box(
                    modifier = Modifier.fillMaxWidth().padding(32.dp),
                    contentAlignment = Alignment.Center,
                ) { CircularProgressIndicator() }

                error != null -> Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(16.dp),
                )

                entries.isEmpty() -> Text(
                    "没有可选项",
                    modifier = Modifier.padding(16.dp),
                )

                else -> LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 520.dp)) {
                    items(entries) { entry ->
                        when (entry) {
                            is PickerEntry.Header -> {
                                Surface(color = Color(0xFFE0E0E0)) {
                                    Text(
                                        entry.text,
                                        color = Color(0xFF616161),
                                        fontWeight = FontWeight.Medium,
                                        style = MaterialTheme.typography.labelLarge,
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .padding(horizontal = 16.dp, vertical = 10.dp),
                                    )
                                }
                            }
                            is PickerEntry.Item -> {
                                Column(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .background(Color.White)
                                        .clickable { onSelect(entry.category) },
                                ) {
                                    Text(
                                        if (entry.category.count > 0) {
                                            "${entry.category.name} (${entry.category.count})"
                                        } else entry.category.name,
                                        color = Color(0xFF212121),
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .padding(horizontal = 16.dp, vertical = 12.dp),
                                    )
                                    Divider(color = Color(0xFFEEEEEE))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
