package com.unison.app.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [NotificationEntity::class], version = 1, exportSchema = false)
abstract class UnisonDatabase : RoomDatabase() {
    abstract fun notificationDao(): NotificationDao

    companion object {
        @Volatile
        private var INSTANCE: UnisonDatabase? = null

        fun getDatabase(context: Context): UnisonDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    UnisonDatabase::class.java,
                    "unison_database"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}

