import Foundation
#if canImport(UserNotifications)
@preconcurrency import UserNotifications
#endif

@MainActor
final class NotificationManager {
    static let shared = NotificationManager()
    private init() {}

    private var hasBundleIdentifier: Bool {
        Bundle.main.bundleIdentifier != nil
    }

    func requestPermissionIfNeeded() {
        #if canImport(UserNotifications)
        guard hasBundleIdentifier else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        #endif
    }

    func syncReminders(for tasks: [TaskItemModel]) {
        #if canImport(UserNotifications)
        guard hasBundleIdentifier else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else {
                return
            }
            let prefix = "tracetodo.task."
            center.getPendingNotificationRequests { pending in
                let oldIds = pending.map(\.identifier).filter { $0.hasPrefix(prefix) }
                if !oldIds.isEmpty {
                    center.removePendingNotificationRequests(withIdentifiers: oldIds)
                }
                let isoFrac = ISO8601DateFormatter()
                isoFrac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                let isoBasic = ISO8601DateFormatter()
                isoBasic.formatOptions = [.withInternetDateTime]

                let now = Date()
                for task in tasks where !task.payload.isDone {
                    guard let offsetMin = task.payload.reminderOffsetMinutes,
                          let dueStr = task.payload.dueAt, !dueStr.isEmpty else {
                        continue
                    }
                    guard let dueDate = isoFrac.date(from: dueStr) ?? isoBasic.date(from: dueStr) else {
                        continue
                    }
                    let fireDate = dueDate.addingTimeInterval(TimeInterval(-offsetMin * 60))
                    guard fireDate > now else { continue }

                    let content = UNMutableNotificationContent()
                    content.title = "⏰ TraceTodo 待办提醒"
                    content.body = task.title
                    content.sound = .default

                    let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: fireDate)
                    let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
                    let req = UNNotificationRequest(
                        identifier: prefix + task.memoryId,
                        content: content,
                        trigger: trigger
                    )
                    center.add(req)
                }
            }
        }
        #endif
    }
}
