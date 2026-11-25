import AppKit
import AVFoundation
import CryptoKit
import Darwin
import Foundation
import SwiftUI
import WebKit
import UniformTypeIdentifiers

// Foundation shortens /private/tmp to /tmp on macOS. The backend requires the
// POSIX canonical spelling; retain that spelling for existing and new folders.
func canonicalNativeFileURL(_ location: URL) throws -> URL {
    let candidate = location.standardizedFileURL
    if let resolved = realpath(candidate.path, nil) {
        defer { free(resolved) }
        return URL(fileURLWithPath: String(cString: resolved), isDirectory: location.hasDirectoryPath)
    }
    let failure = errno
    let parent = candidate.deletingLastPathComponent()
    guard failure == ENOENT, parent.path != candidate.path else {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(failure))
    }
    return try canonicalNativeFileURL(parent).appendingPathComponent(candidate.lastPathComponent, isDirectory: location.hasDirectoryPath)
}

// Branded foreground colors retain readable contrast in both native appearances.
enum BoothBrand {
    static let buttonFill = Color(red: 0.02, green: 0.42, blue: 0.35)
    static let teal = Color(nsColor: NSColor(name: NSColor.Name("BoothBuddyTeal")) { appearance in
        if appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua {
            return NSColor(srgbRed: 0.22, green: 0.78, blue: 0.65, alpha: 1)
        }
        return NSColor(srgbRed: 0.02, green: 0.42, blue: 0.35, alpha: 1)
    })
}

// Native models retain the backend's exact files and diagnostics; they do not rank tracks.
struct NativePlanTrack: Codable, Equatable, Identifiable {
    let id: String
    let title: String
    let artist: String
    let path: String
    let contentHash: String
    let durationS: Double?
}

struct NativePlanPhase: Codable, Equatable {
    let label: String
    let startPosition: Int
    let endPosition: Int
    let durationS: Double
    let deltaS: Double
}

struct NativePlanReason: Codable, Equatable {
    let fileId: String
    let reasons: [String]
}

struct NativePlanAlternative: Codable, Equatable {
    let tracks: [NativePlanTrack]
    let durationS: Double
    let deltaS: Double
    let score: Double
    let reasons: [String]
    let trackReasons: [NativePlanReason]
    let phases: [NativePlanPhase]?
    let transitionEvidence: NativeTransitionReport?
}

struct NativeTransitionPair: Codable, Equatable {
    let fromId: String
    let toId: String
    let bpm: [Double?]
    let camelotKeys: [String?]
    let tempoChangePercent: Double?
    let overlapS: Double?
    let warnings: [String]
    let assumptions: String
}
struct NativeTransitionReport: Codable, Equatable {
    let schemaVersion: Int
    let status: String
    let fullFileRuntimeS: Double?
    let estimatedElapsedS: Double?
    let transitions: [NativeTransitionPair]
    let issues: [NativeJSON]
    let note: String
}

struct NativeIndexedRoot: Decodable, Identifiable {
    let id: String
    let path: String
    let state: String
    let availabilityError: String
    let scanRevision: Int
}
struct NativeIndexedRoots: Decodable { let roots: [NativeIndexedRoot] }
struct NativeInboxFile: Decodable, Identifiable {
    let fileId: String
    let rootId: String
    let relativePath: String
    let status: String
    let revision: Int
    let available: Int
    let error: String
    let metadata: [String: NativeJSON]
    let missingMetadata: [String]
    let exactHashGroupCount: Int
    var id: String { fileId }
    func tag(_ key: String) -> String { guard let value = metadata[key], case let .string(text) = value else { return "" }; return text }
    var title: String { tag("title").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? URL(fileURLWithPath: relativePath).lastPathComponent : tag("title") }
    var needsSearchTags: Bool { ["title", "artist"].contains { tag($0).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
}
struct NativeLibraryInbox: Decodable {
    let items: [NativeInboxFile]
    let counts: [String: Int]
    let limit: Int
    func visibleItems(view: String, query: String, rootId: String) -> [NativeInboxFile] {
        items.filter { file in
            guard rootId.isEmpty || file.rootId == rootId,
                  query.isEmpty || [file.title, file.tag("artist"), file.relativePath].contains(where: { $0.localizedCaseInsensitiveContains(query) }) else { return false }
            switch view {
            case "all": return true
            case "search-tags": return file.needsSearchTags
            case "metadata": return !file.missingMetadata.isEmpty || file.needsSearchTags
            case "unavailable": return file.available == 0
            case "errors": return !file.error.isEmpty
            case "duplicates": return file.exactHashGroupCount > 1
            default: return false
            }
        }
    }
}
struct NativeInboxReviewRequest: Codable, Equatable {
    let requestId: String
    let datasetId: String
    let fileId: String
    let baseRevision: Int
    let status: String
    func validate(dataset: String) throws {
        guard datasetId == dataset, !dataset.isEmpty, !requestId.isEmpty, !fileId.isEmpty,
              baseRevision > 0, baseRevision < Int.max, ["new", "reviewed", "ignored"].contains(status) else {
            throw nativeWorkflowError("Inbox review identity or revision is invalid. No status was sent.")
        }
    }
    func data(dataset: String) throws -> Data {
        try validate(dataset: dataset)
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase; encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(self)
    }
}
struct NativePendingInboxReview: Codable {
    let request: NativeInboxReviewRequest
    let title: String
    let relativePath: String
    var rejected = false
}
struct NativeInboxReviewReceipt: Decodable {
    let ok: Bool
    let requestId: String
    let datasetId: String
    let fileId: String
    let status: String
    let revision: Int
    func validate(_ request: NativeInboxReviewRequest, dataset: String) throws {
        try request.validate(dataset: dataset)
        guard ok, requestId == request.requestId, datasetId == request.datasetId,
              fileId == request.fileId, status == request.status, revision == request.baseRevision + 1 else {
            throw nativeWorkflowError("Inbox status receipt did not match the retained request. Retry exact recovery.")
        }
    }
}

struct NativeScanCancellation: Decodable {
    struct Job: Decodable { let id: String; let status: String }
    let ok: Bool
    let job: Job
    func status(for id: String) throws -> String {
        guard ok, job.id == id, ["cancelled", "cancelling", "completed", "failed", "interrupted"].contains(job.status) else {
            throw nativeWorkflowError("Scan cancellation returned unexpected evidence. Refresh scan status before retrying.")
        }
        return job.status
    }
}

enum NativeAuditionVerification {
    static let maximumBytes = 64 * 1024 * 1024
    static func size(status: Int, etag: String?, range: String?, expectedHash: String) throws -> Int {
        guard expectedHash.count == 64, expectedHash.allSatisfy({ "0123456789abcdef".contains($0) }),
              status == 206, etag == "\"\(expectedHash)\"", let range, range.hasPrefix("bytes 0-0/"),
              let size = Int(range.dropFirst("bytes 0-0/".count)), size > 0, size <= maximumBytes else {
            throw nativeWorkflowError("The selected file changed, is unavailable, or exceeds the 64 MiB native audition limit. Rescan and generate again if its evidence changed.")
        }
        return size
    }
    static func verify(_ data: Data, size: Int, status: Int, etag: String?, range: String?, expectedHash: String) throws {
        guard size > 0, size <= maximumBytes, data.count == size, status == 206,
              etag == "\"\(expectedHash)\"", range == "bytes 0-\(size - 1)/\(size)",
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == expectedHash else {
            throw nativeWorkflowError("Audition bytes do not match the exact file reviewed in this plan. Nothing was played. Rescan and generate again.")
        }
    }
}

struct NativePlanDiagnostics: Codable, Equatable {
    let status: String
    let searchComplete: Bool
    let truncatedBy: [String]
    let inputCount: Int
    let eligibleCount: Int
    let candidateCount: Int
}

struct NativePlanResponse: Codable, Equatable {
    let feasible: Bool
    let alternatives: [NativePlanAlternative]
    let diagnostics: NativePlanDiagnostics
    let unresolved: [NativeJSON]
    let libraryRevision: String
    let plannerVersion: String
}

struct PlanResult: Equatable {
    let response: NativePlanResponse
    let requestData: Data
    let responseData: Data
    var feasible: Bool { response.feasible }
}

indirect enum NativeJSON: Codable, Equatable {
    case object([String: NativeJSON]), array([NativeJSON]), string(String), number(Double), bool(Bool), null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode([NativeJSON].self) { self = .array(value) }
        else { self = .object(try container.decode([String: NativeJSON].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .object(value): try container.encode(value)
        case let .array(value): try container.encode(value)
        case let .string(value): try container.encode(value)
        case let .number(value): try container.encode(value)
        case let .bool(value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var diagnosticText: String {
        switch self {
        case let .object(fields):
            return fields.keys.sorted().map { "\($0.replacingOccurrences(of: "_", with: " ")): \(fields[$0]!.diagnosticText)" }.joined(separator: " · ")
        case let .array(values): return values.map(\.diagnosticText).joined(separator: ", ")
        case let .string(value): return value.replacingOccurrences(of: "_", with: " ")
        case let .number(value): return String(value)
        case let .bool(value): return value ? "Yes" : "No"
        case .null: return "Unknown"
        }
    }
}

struct NativePlanProposal: Codable, Equatable {
    let proposalId: String
    let datasetId: String
    let name: String
    let libraryRevision: String
    let tracks: [NativePlanTrack]
    let expiresAt: Double
}

struct NativeApprovalOperation: Codable, Equatable {
    let proposal: NativePlanProposal
    let requestId: String
    let requestData: Data
    var rejected = false
}

struct NativeApprovalReceipt: Codable {
    let ok: Bool
    let requestId: String
    let datasetId: String
    let proposalId: String
    let crateId: String
    let revision: Int
    let entries: Int
}

struct NativeSavedCrate: Codable, Equatable, Identifiable {
    let id: String
    let revision: Int
    let name: String
    let tracks: [NativePlanTrack]
    let requestId: String
}

struct NativeWorkflowJournal: Codable {
    var schemaVersion = 1
    let datasetId: String
    var pending: NativeApprovalOperation?
    var pendingEdit: NativePendingEdit?
    var pendingInboxReview: NativePendingInboxReview?
    var saved: [NativeSavedCrate] = []
    func validatePendingOperations(dataset: String) throws {
        guard schemaVersion == 1, datasetId == dataset,
              [pending != nil, pendingEdit != nil, pendingInboxReview != nil].filter({ $0 }).count <= 1 else {
            throw nativeWorkflowError("Recovery journal belongs to another dataset/version or contains overlapping mutations.")
        }
        if let pendingInboxReview { try pendingInboxReview.request.validate(dataset: dataset) }
    }
}

struct NativeExportCrate: Codable, Equatable {
    let id: String
    let name: String
    let revision: Int
}

enum NativePlannerDraft {
    static func update(_ text: String, fileID: String, action: String, position: Int = 1) throws -> String {
        guard let data = text.data(using: .utf8),
              var policy = try JSONSerialization.jsonObject(with: data) as? [String: Any], !fileID.isEmpty,
              let required = (policy["must_include"] ?? [String]()) as? [String],
              let excluded = (policy["exclude"] ?? [String]()) as? [String],
              let pins = (policy["pinned"] ?? [[String: Any]]()) as? [[String: Any]] else {
            throw nativeWorkflowError("Correct the existing policy before editing exact-file constraints.")
        }
        var requiredFiles = required, excludedFiles = excluded, pinnedFiles = pins
        switch action {
        case "require", "pin":
            guard !excluded.contains(fileID) else { throw nativeWorkflowError("This file is excluded. Clear its constraints before requiring or pinning it.") }
            if action == "require" {
                if !requiredFiles.contains(fileID) { requiredFiles.append(fileID) }
                guard requiredFiles.count <= 60 else { throw nativeWorkflowError("A plan supports at most 60 required files.") }
            } else {
                let maximum = (policy["max_tracks"] as? NSNumber)?.intValue ?? 20
                guard (1...60).contains(position), position <= maximum else { throw nativeWorkflowError("The pin position must fit the plan's maximum track count.") }
                guard !pins.contains(where: { ($0["position"] as? Int) == position - 1 && ($0["file_id"] as? String) != fileID }) else {
                    throw nativeWorkflowError("That position already has a different pinned file. Clear it before assigning another.")
                }
                pinnedFiles.removeAll { ($0["file_id"] as? String) == fileID }
                pinnedFiles.append(["file_id": fileID, "position": position - 1])
                guard pinnedFiles.count <= 60 else { throw nativeWorkflowError("A plan supports at most 60 pins.") }
            }
        case "exclude":
            guard !required.contains(fileID), !pins.contains(where: { ($0["file_id"] as? String) == fileID }),
                  !((policy["phases"] as? [[String: Any]]) ?? []).contains(where: { ($0["must_include"] as? [String] ?? []).contains(fileID) }) else {
                throw nativeWorkflowError("This file is required or pinned. Remove that requirement before excluding it.")
            }
            if !excludedFiles.contains(fileID) { excludedFiles.append(fileID) }
        case "clear":
            requiredFiles.removeAll { $0 == fileID }; excludedFiles.removeAll { $0 == fileID }
            pinnedFiles.removeAll { ($0["file_id"] as? String) == fileID }
        default: throw nativeWorkflowError("Unknown exact-file draft action.")
        }
        policy["must_include"] = requiredFiles; policy["exclude"] = excludedFiles; policy["pinned"] = pinnedFiles
        return String(decoding: try JSONSerialization.data(withJSONObject: policy, options: [.sortedKeys]), as: UTF8.self)
    }
}

struct NativeRequestImportRow: Codable, Identifiable, Equatable {
    let sourceRow: Int
    var title: String
    var artist: String
    var version: String
    var category: String
    var id: Int { sourceRow }
}

enum NativeRequestIntake {
    static let categories = ["must_play", "do_not_play", "play_if_possible"]
    static func parse(_ input: String, format: String, category: String) throws -> [NativeRequestImportRow] {
        guard input.utf8.count <= 65536, categories.contains(category) else { throw nativeWorkflowError("Use at most 64 KiB of UTF-8 text and a supported request category.") }
        let text = input.hasPrefix("\u{feff}") ? String(input.dropFirst()) : input
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        var rows: [NativeRequestImportRow] = []
        if format == "lines" {
            for (index, line) in normalized.components(separatedBy: "\n").enumerated() where !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                rows.append(NativeRequestImportRow(sourceRow: index + 1, title: line.trimmingCharacters(in: .whitespacesAndNewlines), artist: "", version: "", category: category))
            }
        } else {
            guard format == "csv" || format == "tsv" else { throw nativeWorkflowError("Choose titles, CSV or TSV format.") }
            let delimiter: Character = format == "csv" ? "," : "\t"
            var table: [[String]] = [], fields: [String] = [], value = ""
            var quoted = false, closed = false
            let characters = Array(normalized)
            var index = 0
            while index < characters.count {
                let character = characters[index]
                if quoted {
                    if character == "\"" {
                        if index + 1 < characters.count, characters[index + 1] == "\"" { value.append("\""); index += 1 }
                        else { quoted = false; closed = true }
                    } else { value.append(character) }
                } else if character == delimiter || character == "\n" {
                    fields.append(value); value = ""; closed = false
                    if character == "\n" { table.append(fields); fields = [] }
                } else if character == "\"", value.isEmpty, !closed { quoted = true }
                else {
                    guard !closed, character != "\"" else { throw nativeWorkflowError("Malformed quoted field. Check quotes and separators before importing.") }
                    value.append(character)
                }
                index += 1
            }
            guard !quoted else { throw nativeWorkflowError("A quoted field is not closed.") }
            fields.append(value); table.append(fields)
            guard let first = table.first else { throw nativeWorkflowError("The request list is empty.") }
            let headers = first.map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            guard headers.contains("title"), Set(headers).count == headers.count,
                  Set(headers).isSubset(of: ["title", "artist", "version", "category"]) else {
                throw nativeWorkflowError("CSV/TSV needs a title header and optional artist, version and category headers. Unknown or duplicate columns are refused.")
            }
            for (offset, cells) in table.dropFirst().enumerated() {
                if cells.allSatisfy({ $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) { continue }
                guard cells.count == headers.count else { throw nativeWorkflowError("Row \(offset + 2) has a different number of fields from the header.") }
                let record = Dictionary(uniqueKeysWithValues: zip(headers, cells))
                rows.append(NativeRequestImportRow(sourceRow: offset + 2, title: record["title"] ?? "", artist: record["artist"] ?? "", version: record["version"] ?? "", category: record["category"].flatMap { $0.isEmpty ? nil : $0 } ?? category))
            }
        }
        try validate(rows)
        return rows
    }

    static func validate(_ rows: [NativeRequestImportRow]) throws {
        guard !rows.isEmpty, rows.count <= 200 else { throw nativeWorkflowError("Review between 1 and 200 requests per import.") }
        for row in rows {
            guard !row.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, categories.contains(row.category),
                  [row.title, row.artist, row.version].allSatisfy({ $0.utf8.count <= 1024 && !$0.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) && $0 != "\n" && $0 != "\t" }) }) else {
                throw nativeWorkflowError("Row \(row.sourceRow) needs a title, a supported category and fields of at most 1 KiB without control characters.")
            }
        }
    }

    static func append(_ rows: [NativeRequestImportRow], to eventText: String) throws -> String {
        try validate(rows)
        guard var event = try JSONSerialization.jsonObject(with: Data(eventText.utf8)) as? [String: Any],
              var lists = (event["music_lists"] ?? [String: Any]()) as? [String: Any] else { throw nativeWorkflowError("Correct the existing event JSON before adding requests.") }
        for category in categories {
            guard var existing = (lists[category] ?? [[String: Any]]()) as? [[String: Any]] else { throw nativeWorkflowError("Correct the existing \(category) list before adding requests.") }
            for row in rows where row.category == category {
                var request = ["title": row.title.trimmingCharacters(in: .whitespacesAndNewlines), "artist": row.artist.trimmingCharacters(in: .whitespacesAndNewlines)]
                if !row.version.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { request["version"] = row.version.trimmingCharacters(in: .whitespacesAndNewlines) }
                existing.append(request)
            }
            lists[category] = existing
        }
        event["music_lists"] = lists
        return String(decoding: try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys]), as: UTF8.self)
    }
}

struct NativeExportEntry: Codable, Equatable {
    let position: Int
    let eligible: Bool
    let reason: String
    let path: String?
}

struct NativeExportManifest: Codable, Equatable {
    let schemaVersion: Int
    let datasetId: String
    let crate: NativeExportCrate
    let ready: Bool
    let requested: Int
    let eligible: Int
    let blocked: Int
    let included: Int
    let blockers: [String]
    let entries: [NativeExportEntry]
}

struct NativeExportPreview: Codable, Equatable {
    let ok: Bool
    let previewId: String
    let manifest: NativeExportManifest
}

struct NativeExportDownload: Decodable {
    let ok: Bool
    let previewId: String
    let manifest: NativeExportManifest
    let bundleBase64: String
}

enum NativeSeratoPlaylist {
    static func data(_ manifest: NativeExportManifest) throws -> Data {
        guard manifest.ready, manifest.blocked == 0, manifest.blockers.isEmpty,
              !manifest.entries.isEmpty, manifest.included == manifest.entries.count,
              manifest.requested == manifest.entries.count, manifest.eligible == manifest.entries.count else {
            throw nativeWorkflowError("Only a fully verified export can be sent to Serato.")
        }
        var paths: [String] = []
        for (index, entry) in manifest.entries.enumerated() {
            guard entry.position == index + 1, entry.eligible, entry.reason.isEmpty,
                  let path = entry.path, path.hasPrefix("/"),
                  !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw nativeWorkflowError("Serato export needs exact ordered absolute single-line file paths.")
            }
            paths.append(path)
        }
        return Data(("#EXTM3U\n" + paths.map { $0 + "\n" }.joined()).utf8)
    }
}

enum NativeSeratoCrate {
    static func isSubcratesDestination(_ destination: URL) -> Bool {
        destination.deletingLastPathComponent().lastPathComponent.lowercased() == "subcrates"
    }

    static func filename(for name: String) -> String {
        let cleaned = name.unicodeScalars.map { scalar -> String in
            CharacterSet.controlCharacters.contains(scalar) || scalar == "/" || scalar == ":" ? " " : String(scalar)
        }.joined().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        var stem = ""
        // Leave room for the extension; never split a composed character or exceed
        // common filesystem component byte limits with emoji or combining marks.
        for character in cleaned.trimmingCharacters(in: CharacterSet(charactersIn: ". ")).prefix(100) {
            guard stem.utf8.count + String(character).utf8.count <= 240 else { break }
            stem.append(character)
        }
        return (stem.isEmpty ? "BOOTH BUDDY Set" : stem) + ".crate"
    }

    static func completionMessage(for destination: URL) -> String {
        if isSubcratesDestination(destination) {
            return "Exported \(destination.lastPathComponent). Open Serato and select \(destination.deletingPathExtension().lastPathComponent) in the crate sidebar.\nSaved to: \(destination.path)"
        }
        return "Saved \(destination.lastPathComponent). To load it: quit Serato, move this new file into your library’s _Serato_/Subcrates folder, then reopen Serato and select the crate. Keep the referenced music files available in their current locations.\nSaved to: \(destination.path)"
    }

    static func data(_ manifest: NativeExportManifest) throws -> Data {
        _ = try NativeSeratoPlaylist.data(manifest)
        func record(_ tag: String, _ payload: Data) -> Data {
            var value = Data(tag.utf8)
            var length = UInt32(payload.count).bigEndian
            withUnsafeBytes(of: &length) { value.append(contentsOf: $0) }
            value.append(payload)
            return value
        }
        func text(_ value: String) -> Data { value.data(using: .utf16BigEndian)! }
        var result = record("vrsn", text("1.0/Serato ScratchLive"))
        result.append(record("tvcn", text("1.0")))
        for entry in manifest.entries {
            result.append(record("otrk", Data()))
            result.append(record("ptrk", text(entry.path!)))
        }
        guard result.count <= 64 * 1024 * 1024 else { throw nativeWorkflowError("Serato crate exceeds the supported size.") }
        return result
    }
}

struct NativeExportHistoryItem: Decodable, Identifiable {
    let exportId: String
    let state: String
    let crate: NativeExportCrate?
    let entries: Int?
    let error: String?
    var id: String { exportId }
}

struct NativeExportHistory: Decodable { let exports: [NativeExportHistoryItem]; let total: Int }

func nativeWorkflowError(_ message: String) -> NSError {
    NSError(domain: "BOOTHBUDDY", code: 30, userInfo: [NSLocalizedDescriptionKey: message])
}

func nativeDuration(_ seconds: Double) -> String {
    String(format: "%d:%02d", Int(seconds) / 60, abs(Int(seconds)) % 60)
}

struct NativeMembership: Codable, Equatable {
    let id: String
    let snapshotId: String
    let songId: String
    var body: [String: Any] { ["id": id, "snapshot_id": snapshotId, "song_id": songId] }
}
struct NativeCrateSnapshot: Codable, Equatable {
    let id: String
    let name: String
    let revision: Int
    let entries: [NativeMembership]
}
struct NativeCrateReceipt: Codable, Equatable {
    let crateId: String
    let baseRevision: Int
    let revision: Int
    let name: String
    let entries: [NativeMembership]
    var body: [String: Any] {
        ["crate_id": crateId, "base_revision": baseRevision, "revision": revision,
         "name": name, "entries": entries.map(\.body)]
    }
}
struct NativeEditPreview: Codable, Equatable {
    let editId: String
    let datasetId: String
    let before: [String: NativeCrateSnapshot]
    let after: [String: NativeCrateSnapshot]
    let expiresAt: Double
    func expectedCrates(undo: Bool) -> [NativeCrateReceipt] {
        let desired = undo ? before : after
        return desired.keys.sorted().map { id in
            let base = undo ? after[id]!.revision + 1 : before[id]!.revision
            return NativeCrateReceipt(crateId: id, baseRevision: base, revision: base + 1,
                                      name: desired[id]!.name, entries: desired[id]!.entries)
        }
    }
}
struct NativeEditReceipt: Codable {
    let ok: Bool
    let requestId: String
    let datasetId: String
    let editId: String
    let operation: String
    let crates: [NativeCrateReceipt]
}
struct NativePendingEdit: Codable {
    let preview: NativeEditPreview
    let undo: Bool
    let requestId: String
    let requestData: Data
    var rejected = false
}
struct NativeEditHistoryItem: Decodable, Identifiable {
    let editId: String
    let document: NativeEditDocument
    let approval: NativeEditReceipt?
    let undo: NativeEditReceipt?
    var id: String { editId }
    var preview: NativeEditPreview {
        NativeEditPreview(editId: editId, datasetId: document.datasetId,
                          before: document.before, after: document.after, expiresAt: document.expiresAt)
    }
}
struct NativeEditDocument: Decodable {
    let datasetId: String
    let before: [String: NativeCrateSnapshot]
    let after: [String: NativeCrateSnapshot]
    let expiresAt: Double
}
struct NativeCrateSong: Decodable { let title: String?; let artist: String? }
struct NativeCrateFile: Decodable { let id: String; let path: String; let contentHash: String }
struct NativeCrateTrack: Decodable, Identifiable {
    let id: String
    let snapshotId: String
    let songId: String
    let song: NativeCrateSong
    let selectedFile: NativeCrateFile?
    var membership: NativeMembership { NativeMembership(id: id, snapshotId: snapshotId, songId: songId) }
}
struct NativeCrateDetail: Decodable {
    let id: String
    let name: String
    let revision: Int
    let entries: [NativeCrateTrack]
    var snapshot: NativeCrateSnapshot {
        NativeCrateSnapshot(id: id, name: name, revision: revision, entries: entries.map(\.membership))
    }
}

struct ScanJob: Decodable, Identifiable {
    let id: String
    let path: String
    let status: String
    let scanned: Int
    let errors: Int
    let error: String?
    var active: Bool { ["queued", "running", "cancelling"].contains(status) }
}

struct NativeScanHistory {
    let jobs: [ScanJob]
    let expanded: Bool
    var active: [ScanJob] { jobs.filter(\.active) }
    var inactive: [ScanJob] { jobs.filter { !$0.active } }
    var visibleHistory: [ScanJob] { expanded ? inactive : Array(inactive.prefix(5)) }
    var hiddenCount: Int { inactive.count - visibleHistory.count }
}

struct CrateSummary: Identifiable, Equatable {
    let id: String
    let name: String
    let entries: Int
    let revision: Int
}

struct CrateEntrySummary: Identifiable, Equatable {
    let id: String
    let title: String
    let artist: String
    let matchState: String
    let selectedPath: String?
}

struct CrateEditPreview: Equatable {
    let editID: String
    let beforeName: String
    let afterName: String
    let revision: Int
    let entryCount: Int
}

struct WorkspaceSummary: Equatable {
    let rules: Int
    let crates: Int
    let crateItems: [CrateSummary]
    let history: Int
    let repairs: Int
    let pendingReleaseGates: Int
    let commercialReady: Bool
    let healthCounts: [String: Int]
    let healthAvailable: Bool
    let gates: [CapabilityItem]
    let destinations: [CapabilityItem]
}

struct CapabilityItem: Identifiable, Equatable {
    let id: String
    let state: String
    let detail: String
    let name: String
}

struct CatalogTrack: Identifiable {
    let id: String
    let title: String
    let artist: String
    let path: String
    let metadata: String
}

@MainActor
final class WorkspaceDrafts: ObservableObject {
    @Published var rootPath = ""
    @Published var scanMessage = ""
    @Published var isScanning = false
    @Published var eventJSON = "{\"music_lists\":{\"must_play\":[],\"do_not_play\":[],\"client_playlists\":[]}}"
    @Published var policyJSON = "{\"target_minutes\":30,\"tolerance_minutes\":3,\"max_tracks\":20,\"clean_only\":false,\"must_include\":[],\"exclude\":[],\"pinned\":[]}"
    @Published var planMessage = ""
    @Published var planResult: PlanResult?
    @Published var isPlanning = false
    @Published var query = ""
    @Published var cleanOnly = false
    @Published var plannerQuery = ""
    @Published var plannerLimit = 1000
    @Published var plannedCrateName = "New planned set"
}

enum WorkspacePhase: Equatable {
    case starting
    case loading
    case ready
    case failed(String)
}

// Memory-only, bounded audio transport. No audio is written to an app cache.
final class NativeAuditionDownload: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let maximum: Int
    private let lock = NSLock()
    private var canceled = false
    private var task: URLSessionDataTask?
    private var session: URLSession?
    private var continuation: CheckedContinuation<(Data, HTTPURLResponse), Error>?
    private var bytes = Data()
    private var response: HTTPURLResponse?
    private var failure: Error?
    init(maximum: Int) { self.maximum = maximum }

    static func load(_ request: URLRequest, maximum: Int) async throws -> (Data, HTTPURLResponse) {
        let transfer = NativeAuditionDownload(maximum: maximum)
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in transfer.start(request, continuation: continuation) }
        }, onCancel: { transfer.cancel() })
    }
    private func start(_ request: URLRequest, continuation: CheckedContinuation<(Data, HTTPURLResponse), Error>) {
        lock.lock()
        if canceled { lock.unlock(); continuation.resume(throwing: CancellationError()); return }
        self.continuation = continuation
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil; configuration.httpCookieStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 30; configuration.timeoutIntervalForResource = 60
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
        self.session = session
        let task = session.dataTask(with: request); self.task = task
        lock.unlock(); task.resume()
    }
    private func cancel() { lock.lock(); canceled = true; let task = task; lock.unlock(); task?.cancel() }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse, response.expectedContentLength <= Int64(maximum) else {
            failure = nativeWorkflowError("The audition response exceeded its size limit."); completionHandler(.cancel); return
        }
        self.response = http; completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard data.count <= maximum - bytes.count else {
            failure = nativeWorkflowError("The audition response exceeded its size limit."); dataTask.cancel(); return
        }
        bytes.append(data)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock(); let completion = continuation; continuation = nil; self.task = nil; lock.unlock()
        session.finishTasksAndInvalidate(); self.session = nil
        if let error = failure ?? error { completion?.resume(throwing: error) }
        else if let response { completion?.resume(returning: (bytes, response)) }
        else { completion?.resume(throwing: nativeWorkflowError("Audition returned no HTTP response.")) }
    }
}

@MainActor
final class NativeAuditionPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var activeTrackId: String?
    @Published private(set) var ownerIndex: Int?
    @Published private(set) var loading = false
    @Published private(set) var playing = false
    @Published private(set) var duration = 0.0
    @Published var message = ""
    private var audio: AVAudioPlayer?
    private var pending: Task<Void, Never>?
    private var generation = UUID()
    func play(_ track: NativePlanTrack, controller: BackendController, ownerIndex: Int) {
        stop()
        let generation = self.generation
        activeTrackId = track.id; self.ownerIndex = ownerIndex; loading = true; message = "Verifying selected audio…"
        pending = Task {
            do {
                let bytes = try await controller.nativeAuditionBytes(track)
                try Task.checkCancellation()
                guard generation == self.generation else { return }
                let audio = try AVAudioPlayer(data: bytes)
                guard audio.duration.isFinite, audio.duration > 0 else { throw nativeWorkflowError("This audio has no playable duration.") }
                audio.delegate = self; audio.volume = 0.5
                guard audio.prepareToPlay(), audio.play() else { throw nativeWorkflowError("macOS could not start this audio. Check the output device or supported codec.") }
                self.audio = audio; duration = audio.duration; playing = true; loading = false
                message = "Auditioning \(track.title) — \(track.artist). Listening does not approve timing or certify a transition."
            } catch {
                guard generation == self.generation else { return }
                loading = false; playing = false; message = error.localizedDescription
            }
        }
    }
    func togglePause() {
        guard let audio else { return }
        if playing { audio.pause(); playing = false }
        else { playing = audio.play(); if !playing { message = "The output device could not resume playback." } }
    }
    var currentTime: Double { audio?.currentTime ?? 0 }
    func seek(_ value: Double) { if value.isFinite { audio?.currentTime = min(duration, max(0, value)) } }
    func stop() {
        generation = UUID(); pending?.cancel(); pending = nil; audio?.stop(); audio = nil
        activeTrackId = nil; ownerIndex = nil; loading = false; playing = false; duration = 0; message = ""
    }
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.audio === player else { return }
            self.playing = false; self.message = flag ? "Audition ended. No timing or cue review was recorded." : "Audio playback ended with a decoding error."
        }
    }
}

@MainActor
final class BackendController: NSObject, ObservableObject, WKNavigationDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    var onPhaseChange: ((WorkspacePhase) -> Void)?
    private weak var webView: WKWebView?
    private var backend: Process?
    private var lockFD: Int32 = -1
    private var origin: URL?
    private var token = ""
    private var datasetID = ""
    private var dataPath = ""
    private var started = false
    private var closing = false
    private var failing = false
    private var attempts = 0
    private var requestedWorkspacePath: String?
    @Published private(set) var workspaceSelectionRevision = 0
    @Published private(set) var workflowJournal: NativeWorkflowJournal?
    @Published private(set) var workflowJournalError = ""
    @Published private(set) var approvalBusy = false
    @Published private(set) var nativeExportRevision = 0
    private var simulatedAcknowledgementLoss = false
    private var simulatedEditAcknowledgementLoss = false
    private var simulatedInboxAcknowledgementLoss = false
    @Published private(set) var nativeWorkspaceRevision = 0

    func makeWebView() -> WKWebView {
        let userContentController = WKUserContentController()
        userContentController.add(self, name: "selectMusicFolder")
        userContentController.addUserScript(WKUserScript(
            source: """
            (() => {
              const field = document.getElementById('root');
              if (field && !document.getElementById('native-folder-picker')) {
                const button = document.createElement('button');
                button.id = 'native-folder-picker';
                button.type = 'button';
                button.textContent = 'Choose music folder…';
                button.addEventListener('click', () =>
                  window.webkit.messageHandlers.selectMusicFolder.postMessage('choose'));
                field.insertAdjacentElement('afterend', button);
              }
              const style = document.createElement('style');
              style.textContent = '.sidebar,.topbar{display:none!important} main{margin-left:0!important;padding-top:24px!important}';
              document.head.appendChild(style);
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        ))
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = userContentController
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.allowsBackForwardNavigationGestures = true
        webView = view
        if started, let origin {
            view.load(URLRequest(url: origin))
        }
        return view
    }

    func start() {
        guard backend == nil, !started, !closing else { return }
        update(.starting)
        do {
            let support: URL
            if ProcessInfo.processInfo.arguments.contains("--synthetic-data"),
               ProcessInfo.processInfo.environment["AUTOCUE_DESKTOP_DATA"]?.hasPrefix("/") != true {
                fail("Synthetic runs require an absolute temporary data-directory override.")
                return
            }
            if let selected = requestedWorkspacePath {
                support = URL(fileURLWithPath: selected, isDirectory: true)
            } else if let override = ProcessInfo.processInfo.environment["AUTOCUE_DESKTOP_DATA"], !override.isEmpty {
                guard override.hasPrefix("/") else {
                    fail("The development data-directory override must be an absolute path.")
                    return
                }
                support = URL(fileURLWithPath: override, isDirectory: true)
            } else if !ProcessInfo.processInfo.arguments.contains("--ui-testing"),
                      let selected = UserDefaults.standard.string(forKey: "booth-buddy-active-workspace") {
                support = URL(fileURLWithPath: selected, isDirectory: true)
                guard selected.hasPrefix("/"), FileManager.default.fileExists(atPath: support.appendingPathComponent("library.sqlite3").path) else {
                    fail("The selected workspace is unavailable. Reconnect its storage or open the default workspace.")
                    return
                }
            } else {
                support = FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Library/Application Support/AutoCue Studio", isDirectory: true)
            }
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            dataPath = try canonicalNativeFileURL(support).path
            lockFD = open((dataPath as NSString).appendingPathComponent(".desktop.lock"),
                          O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
            guard lockFD >= 0 else {
                fail("Could not open the BOOTH BUDDY data-directory lock. Check permissions and available disk space.")
                return
            }
            guard flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
                fail(errno == EWOULDBLOCK || errno == EAGAIN
                     ? "Another BOOTH BUDDY instance is using this data directory. Quit it before reopening the app."
                     : "Could not lock the BOOTH BUDDY data directory. Check its permissions and reopen the app.")
                return
            }
            launchBackend()
        } catch {
            fail("Could not initialize BOOTH BUDDY’s local data directory: \(error.localizedDescription)")
        }
    }

    func retry() {
        stop()
        closing = false
        failing = false
        started = false
        attempts = 0
        start()
    }

    func stop() {
        closing = true
        if let process = backend, process.isRunning {
            process.terminate()
            let deadline = Date().addingTimeInterval(3)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
        }
        backend = nil
        started = false
        origin = nil
        token = ""
        datasetID = ""
        if lockFD >= 0 { close(lockFD); lockFD = -1 }
    }

    func navigate(to section: String) {
        guard started, ["library", "planning", "crates", "operations", "readiness"].contains(section) else {
            return
        }
        webView?.evaluateJavaScript(
            "document.querySelector('.sidebar nav a[href=\"#\(section)\"]')?.click()"
        )
    }

    func reload() {
        if started {
            webView?.reload()
        } else {
            retry()
        }
    }

    private func launchBackend() {
        attempts += 1
        do {
            let port = try freePort()
            let base = URL(string: "http://127.0.0.1:\(port)")!
            let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/backend/studio-backend")
            let process = Process()
            process.executableURL = executable
            process.arguments = ["--data", dataPath, "--port", String(port)]
            var environment = ProcessInfo.processInfo.environment
            for key in environment.keys where ["PYTHON", "AUTOCUE_", "DYLD_", "XCTest", "XCInject", "__XCODE", "LLVM_PROFILE_"].contains(where: key.hasPrefix) {
                environment.removeValue(forKey: key)
            }
            environment["AUTOCUE_DESKTOP_PARENT"] = String(getpid())
            process.environment = environment
            process.currentDirectoryURL = URL(fileURLWithPath: dataPath, isDirectory: true)
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            if ProcessInfo.processInfo.arguments.contains("--ui-testing"), ProcessInfo.processInfo.arguments.contains("--synthetic-data") {
                let diagnostics = URL(fileURLWithPath: dataPath).appendingPathComponent("synthetic-backend-stderr.log")
                if FileManager.default.createFile(atPath: diagnostics.path, contents: nil, attributes: [.posixPermissions: 0o600]),
                   let log = try? FileHandle(forWritingTo: diagnostics) { process.standardError = log }
            }
            backend = process
            process.terminationHandler = { [weak self, weak process] _ in
                Task { @MainActor [weak self, weak process] in
                    guard let self, let process, self.backend === process, !self.closing, !self.failing else { return }
                    if !self.started && self.attempts < 3 { self.launchBackend() }
                    else { self.fail("The local BOOTH BUDDY service stopped unexpectedly. Reopen the app.") }
                }
            }
            try process.run()
            checkReady(base, process: process, remaining: 50)
            Task { @MainActor [weak self, weak process] in
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard let self, let process, self.backend === process, !self.started,
                      !self.closing, !self.failing else { return }
                self.fail("The local BOOTH BUDDY service did not start within 30 seconds. Check the data directory and reopen the app.")
            }
        } catch {
            fail("Could not start the bundled BOOTH BUDDY service: \(error.localizedDescription)")
        }
    }

    private func checkReady(_ base: URL, process: Process, remaining: Int) {
        var request = URLRequest(url: base.appendingPathComponent("api/session"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self, weak process] data, response, _ in
            Task { @MainActor [weak self, weak process] in
                guard let self, let process, self.backend === process, process.isRunning,
                      !self.closing, !self.failing else { return }
                if let data, let http = response as? HTTPURLResponse, http.statusCode == 200,
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let token = json["token"] as? String {
                    self.origin = base
                    self.token = token
                    self.datasetID = json["dataset_id"] as? String ?? ""
                    self.started = true
                    self.loadWorkflowJournal()
                    // Native SwiftUI is the primary workspace; WebKit is only loaded on demand.
                    self.update(.ready)
                    self.webView?.load(URLRequest(url: base))
                } else if remaining > 0 {
                    try? await Task.sleep(nanoseconds: 100_000_000)
                    self.checkReady(base, process: process, remaining: remaining - 1)
                } else {
                    self.fail("The local BOOTH BUDDY service did not respond. Check local network restrictions and reopen the app.")
                }
            }
        }.resume()
    }

    func loadSummary(completion: @escaping (Result<WorkspaceSummary, Error>) -> Void) {
        guard let origin, !token.isEmpty else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 7,
                                        userInfo: [NSLocalizedDescriptionKey: "The local workspace is not ready yet."])))
            return
        }
        var request = URLRequest(url: origin.appendingPathComponent("api/studio/state"))
        request.timeoutInterval = 30
        request.setValue(token, forHTTPHeaderField: "X-AutoCue-Token")
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<WorkspaceSummary, Error>
            if let error {
                result = .failure(error)
            } else if let data, let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let rules = (json["rules"] as? [[String: Any]] ?? []).count
                let crateItems = (json["crates"] as? [[String: Any]] ?? []).compactMap { crate -> CrateSummary? in
                    guard let id = crate["id"] as? String,
                          let name = crate["name"] as? String else { return nil }
                    return CrateSummary(
                        id: id,
                        name: name,
                        entries: crate["entries"] as? Int ?? 0,
                        revision: crate["revision"] as? Int ?? 0
                    )
                }
                let crates = crateItems.count
                let history = (json["history"] as? [[String: Any]] ?? []).count
                let repairs = (json["file_repairs"] as? [[String: Any]] ?? []).count
                let capabilities = json["capabilities"] as? [String: Any]
                let gates = (capabilities?["release_gates"] as? [[String: Any]] ?? [])
                    .filter { ($0["state"] as? String) != "complete" }.count
                result = .success(WorkspaceSummary(
                    rules: rules, crates: crates, crateItems: crateItems,
                    history: history, repairs: repairs,
                    pendingReleaseGates: gates,
                    commercialReady: capabilities?["commercial_ready"] as? Bool ?? false,
                    healthCounts: (json["health"] as? [String: Any])?["counts"] as? [String: Int] ?? [:],
                    healthAvailable: (json["health"] as? [String: Any])?["state"] as? String == "ok",
                    gates: Self.capabilityItems(capabilities?["release_gates"], detailKey: "requirement"),
                    destinations: Self.capabilityItems(capabilities?["destinations"], detailKey: "note")
                ))
            } else {
                result = .failure(NSError(domain: "BOOTHBUDDY", code: 8,
                                          userInfo: [NSLocalizedDescriptionKey: "Workspace state could not be loaded."]))
            }
            DispatchQueue.main.async {
                completion(result)
            }
        }.resume()
    }

    func loadCrateEntries(crateID: String, completion: @escaping (Result<[CrateEntrySummary], Error>) -> Void) {
        guard let origin, !token.isEmpty,
              var components = URLComponents(url: origin.appendingPathComponent("api/catalog/personal/detail"),
                                              resolvingAgainstBaseURL: false) else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 9,
                                        userInfo: [NSLocalizedDescriptionKey: "The local workspace is not ready yet."])))
            return
        }
        components.queryItems = [URLQueryItem(name: "crate_id", value: crateID)]
        guard let url = components.url else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 10,
                                        userInfo: [NSLocalizedDescriptionKey: "The crate detail request could not be prepared."])))
            return
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.setValue(token, forHTTPHeaderField: "X-AutoCue-Token")
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<[CrateEntrySummary], Error>
            if let error {
                result = .failure(error)
            } else if let data, let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let entries = (json["entries"] as? [[String: Any]] ?? []).compactMap { entry -> CrateEntrySummary? in
                    guard let id = entry["id"] as? String else { return nil }
                    let song = entry["song"] as? [String: Any]
                    let title = song?["title"] as? String ?? "Untitled track"
                    let artist = song?["artist"] as? String ?? "Unknown artist"
                    let selectedFile = entry["selected_file"] as? [String: Any]
                    return CrateEntrySummary(
                        id: id,
                        title: title,
                        artist: artist,
                        matchState: entry["match_state"] as? String ?? "unreviewed",
                        selectedPath: selectedFile?["path"] as? String
                    )
                }
                result = .success(entries)
            } else {
                result = .failure(NSError(domain: "BOOTHBUDDY", code: 11,
                                          userInfo: [NSLocalizedDescriptionKey: "Crate detail could not be loaded."]))
            }
            DispatchQueue.main.async {
                completion(result)
            }
        }.resume()
    }

    func previewCrateRename(crateID: String, revision: Int, name: String,
                            completion: @escaping (Result<CrateEditPreview, Error>) -> Void) {
        guard let origin, !token.isEmpty, !datasetID.isEmpty else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 12,
                                        userInfo: [NSLocalizedDescriptionKey: "The local workspace is not ready yet."])))
            return
        }
        let body: [String: Any] = [
            "dataset_id": datasetID,
            "operations": [[
                "action": "rename",
                "crate_id": crateID,
                "base_revision": revision,
                "name": name
            ]]
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 13,
                                        userInfo: [NSLocalizedDescriptionKey: "The rename preview could not be prepared."])))
            return
        }
        var request = URLRequest(url: origin.appendingPathComponent("api/studio/edit/preview"))
        request.httpMethod = "POST"
        request.httpBody = data
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 30
        request.setValue(token, forHTTPHeaderField: "X-AutoCue-Token")
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<CrateEditPreview, Error>
            if let error {
                result = .failure(error)
            } else if let data, let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let editID = json["edit_id"] as? String,
                      let before = json["before"] as? [String: Any],
                      let after = json["after"] as? [String: Any],
                      let beforeCrate = before[crateID] as? [String: Any],
                      let afterCrate = after[crateID] as? [String: Any] {
                result = .success(CrateEditPreview(
                    editID: editID,
                    beforeName: beforeCrate["name"] as? String ?? "Current crate",
                    afterName: afterCrate["name"] as? String ?? name,
                    revision: afterCrate["revision"] as? Int ?? revision,
                    entryCount: (afterCrate["entries"] as? [[String: Any]] ?? []).count
                ))
            } else {
                let message = Self.responseMessage(data, fallback: "The rename preview could not be created.")
                result = .failure(NSError(domain: "BOOTHBUDDY", code: 14,
                                          userInfo: [NSLocalizedDescriptionKey: message]))
            }
            DispatchQueue.main.async {
                completion(result)
            }
        }.resume()
    }

    func generatePlan(event: String, policy: String, query: String, limit: Int,
                      completion: @escaping (Result<PlanResult, Error>) -> Void) {
        Task {
            do {
                guard let eventData = event.data(using: .utf8),
                      let policyData = policy.data(using: .utf8),
                      let eventJSON = try JSONSerialization.jsonObject(with: eventData) as? [String: Any],
                      let policyJSON = try JSONSerialization.jsonObject(with: policyData) as? [String: Any] else {
                    throw nativeWorkflowError("Enter valid event and policy JSON objects.")
                }
                let rule: [String: Any] = ["q": query, "limit": limit, "sort": "title"]
                let scope = try await nativeRequest("api/studio/search", body: ["rule": rule])
                guard let total = scope["total"] as? Int, total >= 0 else {
                    throw nativeWorkflowError("The local search returned incomplete candidate counts.")
                }
                guard total <= limit else {
                    throw nativeWorkflowError("\(total) matching files exceed the candidate limit of \(limit). Narrow the candidate search before generating; no tracks were silently omitted.")
                }
                // /plan is read-only and does not accept dataset_id.
                let body: [String: Any] = ["rule": rule, "event": eventJSON, "policy": policyJSON,
                                          "playback": ["windows": [], "overlaps": []], "optimize_playback": false]
                let requestData = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
                let data = try await nativeBytes("api/studio/plan", bodyData: requestData)
                let response = try Self.decodeNative(NativePlanResponse.self, data)
                guard response.libraryRevision == scope["library_revision"] as? String else {
                    throw nativeWorkflowError("The library changed during planning. Generate again against the current index.")
                }
                guard ["feasible", "infeasible", "search_exhausted"].contains(response.diagnostics.status),
                      response.feasible == !response.alternatives.isEmpty,
                      response.feasible == (response.diagnostics.status == "feasible") else {
                    throw nativeWorkflowError("The planner returned inconsistent diagnostics.")
                }
                completion(.success(PlanResult(response: response, requestData: requestData, responseData: data)))
            } catch { completion(.failure(error)) }
        }
    }

    func scan(path: String, completion: @escaping (Result<String, Error>) -> Void) {
        guard let origin, !token.isEmpty, !datasetID.isEmpty else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 1,
                                        userInfo: [NSLocalizedDescriptionKey: "The local workspace is not ready yet."])))
            return
        }
        let requestID = UUID().uuidString.lowercased()
        let body: [String: Any] = [
            "path": path,
            "dataset_id": datasetID,
            "request_id": requestID
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else {
            completion(.failure(NSError(domain: "BOOTHBUDDY", code: 2,
                                        userInfo: [NSLocalizedDescriptionKey: "The scan request could not be prepared."])))
            return
        }
        var request = URLRequest(url: origin.appendingPathComponent("api/catalog/scan"))
        request.httpMethod = "POST"
        request.httpBody = data
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 30
        request.setValue(token, forHTTPHeaderField: "X-AutoCue-Token")
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<String, Error>
            if let error {
                result = .failure(error)
            } else if let data, let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let jobID = json["job_id"] as? String {
                result = .success("Scan started · \(jobID.prefix(8))")
            } else {
                let message = Self.responseMessage(data, fallback: "The scan could not be started.")
                result = .failure(NSError(domain: "BOOTHBUDDY", code: 3,
                                          userInfo: [NSLocalizedDescriptionKey: message]))
            }
            DispatchQueue.main.async {
                completion(result)
            }
        }.resume()
    }

    nonisolated private static func responseMessage(_ data: Data?, fallback: String) -> String {
        guard let data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let message = json["error"] as? String, !message.isEmpty else { return fallback }
        return String(message.prefix(1500))
    }

    nonisolated private static func capabilityItems(_ value: Any?, detailKey: String) -> [CapabilityItem] {
        (value as? [[String: Any]] ?? []).compactMap { item in
            guard let id = item["id"] as? String, let state = item["state"] as? String else { return nil }
            return CapabilityItem(id: id, state: state, detail: item[detailKey] as? String ?? "",
                                  name: item["name"] as? String ?? id.replacingOccurrences(of: "-", with: " ").capitalized)
        }
    }

    nonisolated private static func decodeNative<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do { return try decoder.decode(type, from: data) }
        catch { throw nativeWorkflowError("The local service returned incomplete or unexpected evidence. Refresh and retry.") }
    }

    private func nativeBytes(_ endpoint: String, bodyData: Data? = nil,
                             query: [URLQueryItem] = []) async throws -> Data {
        guard started, let origin, !token.isEmpty, !closing else {
            throw nativeWorkflowError("The local workspace is not ready. Retry after it starts.")
        }
        var components = URLComponents(url: origin.appendingPathComponent(endpoint), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue(token, forHTTPHeaderField: "X-AutoCue-Token")
        if let bodyData {
            request.httpMethod = "POST"
            request.httpBody = bodyData
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw NSError(domain: "BOOTHBUDDY", code: (response as? HTTPURLResponse)?.statusCode ?? 21,
                          userInfo: [NSLocalizedDescriptionKey: Self.responseMessage(data, fallback: "The local request failed. Please retry.")])
        }
        return data
    }

    private func nativeRequest(_ endpoint: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        let bodyData = try body.map { try JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
        let data = try await nativeBytes(endpoint, bodyData: bodyData)
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw nativeWorkflowError("The local service returned an unexpected response.")
        }
        return json
    }

    func searchCatalog(query: String, cleanOnly: Bool) async throws -> (tracks: [CatalogTrack], total: Int) {
        let json = try await nativeRequest("api/studio/search", body: ["rule": ["q": query, "clean_only": cleanOnly, "limit": 100, "sort": "title"]])
        guard let rows = json["tracks"] as? [[String: Any]], let total = json["total"] as? Int else {
            throw NSError(domain: "BOOTHBUDDY", code: 23, userInfo: [NSLocalizedDescriptionKey: "Catalog results could not be read."])
        }
        let tracks = rows.compactMap { row -> CatalogTrack? in
            guard let id = row["id"] as? String else { return nil }
            let bpm = (row["bpm"] as? NSNumber).map { "\($0) BPM" } ?? "BPM unknown"
            let key = (row["key"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "Key unknown"
            return CatalogTrack(id: id, title: (row["title"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? URL(fileURLWithPath: row["path"] as? String ?? "").deletingPathExtension().lastPathComponent,
                                artist: (row["artist"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "Unknown artist", path: row["path"] as? String ?? "",
                                metadata: "\(bpm) · \(key) · Indexed metadata")
        }
        return (tracks, total)
    }

    func scanJobs() async throws -> [ScanJob] {
        let json = try await nativeRequest("api/catalog/scans")
        guard let rows = json["jobs"] as? [[String: Any]] else {
            throw NSError(domain: "BOOTHBUDDY", code: 24, userInfo: [NSLocalizedDescriptionKey: "Scan progress could not be read."])
        }
        return rows.compactMap { row in
            guard let id = row["id"] as? String, let status = row["status"] as? String else { return nil }
            let progress = row["progress"] as? [String: Any] ?? [:]
            return ScanJob(id: id, path: row["path"] as? String ?? "", status: status,
                           scanned: progress["scanned"] as? Int ?? 0, errors: progress["errors"] as? Int ?? 0,
                           error: row["error"] as? String)
        }
    }

    func nativeIndexedRoots() async throws -> [NativeIndexedRoot] {
        try Self.decodeNative(NativeIndexedRoots.self, await nativeBytes("api/catalog/roots")).roots
    }
    func nativeLibraryInbox() async throws -> NativeLibraryInbox {
        try Self.decodeNative(NativeLibraryInbox.self, await nativeBytes("api/studio/inbox", bodyData: Data("{}".utf8)))
    }
    func cancelNativeScan(_ id: String) async throws -> String {
        let body = try JSONSerialization.data(withJSONObject: ["dataset_id": datasetID, "job_id": id])
        let receipt = try Self.decodeNative(NativeScanCancellation.self, await nativeBytes("api/catalog/scans/cancel", bodyData: body))
        return try receipt.status(for: id)
    }

    private func freePort() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw NSError(domain: "port", code: 1) }
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr = in_addr(s_addr: in_addr_t(INADDR_LOOPBACK).bigEndian)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0 else { throw NSError(domain: "port", code: 2) }
        var size = socklen_t(MemoryLayout<sockaddr_in>.size)
        guard withUnsafeMutablePointer(to: &address, { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            getsockname(fd, $0, &size)
        } }) == 0 else { throw NSError(domain: "port", code: 3) }
        return UInt16(bigEndian: address.sin_port)
    }

    private func local(_ url: URL?) -> Bool {
        guard let url, let origin else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == origin.port
    }

    private func update(_ phase: WorkspacePhase) {
        onPhaseChange?(phase)
    }

    private func fail(_ message: String) {
        guard !closing, !failing else { return }
        failing = true
        update(.failed(message))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard started, local(webView.url), !closing, !failing else { return }
        update(.ready)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        handleNavigationFailure(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handleNavigationFailure(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        fail("The BOOTH BUDDY workspace stopped unexpectedly. Reopen the app.")
    }

    private func handleNavigationFailure(_ error: Error) {
        let nsError = error as NSError
        guard nsError.domain != NSURLErrorDomain || nsError.code != NSURLErrorCancelled else { return }
        fail("The BOOTH BUDDY workspace could not load: \(error.localizedDescription)")
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "selectMusicFolder", message.frameInfo.isMainFrame,
              local(message.frameInfo.request.url), message.body as? String == "choose" else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if let hostWindow = webView?.window {
            panel.beginSheetModal(for: hostWindow) { [weak self] result in
                guard let self, result == .OK, let url = panel.url,
                      let encoded = try? JSONSerialization.data(withJSONObject: [url.path]),
                      let json = String(data: encoded, encoding: .utf8) else { return }
                self.webView?.evaluateJavaScript("document.getElementById('root').value = \(json)[0]")
            }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.shouldPerformDownload {
            let trustedBlob = url.scheme == "blob" && local(action.sourceFrame.request.url)
            decisionHandler((local(url) || trustedBlob) ? .download : .cancel)
        } else if local(url) && action.targetFrame?.isMainFrame == true {
            decisionHandler(.allow)
        } else {
            if action.navigationType == .linkActivated,
               ["http", "https"].contains(url.scheme?.lowercased() ?? ""), !local(url) {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = response.response.url,
              local(url) || (url.scheme == "blob" && local(webView.url)) else {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        if let hostWindow = webView?.window {
            panel.beginSheetModal(for: hostWindow) { result in completionHandler(result == .OK ? panel.url : nil) }
        } else {
            completionHandler(nil)
        }
    }
}

struct WebWorkspaceView: NSViewRepresentable {
    let controller: BackendController

    func makeNSView(context: Context) -> WKWebView {
        controller.makeWebView()
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

struct BoothBuddyRootView: View {
    @Binding var phase: WorkspacePhase
    @ObservedObject var controller: BackendController
    @StateObject private var drafts = WorkspaceDrafts()
    @State private var refreshRevision = 0
    @State private var selectedSection = "library"
    @State private var showDetailedWorkspace = false

    var body: some View {
        HSplitView {
            BoothBuddySidebar(selectedSection: $selectedSection) { section in
                controller.navigate(to: section)
            }
            VStack(spacing: 0) {
                WorkspaceHeader(title: sectionTitle, phase: phase, reload: {
                    controller.reload()
                    refreshRevision += 1
                },
                                showDetailedWorkspace: $showDetailedWorkspace)
                Divider()
                ZStack {
                    if showDetailedWorkspace && !isFailure {
                        NativeDetailedWorkspaceView(
                            section: selectedSection,
                            controller: controller
                        )
                    } else if case .starting = phase {
                        LoadingView(title: "BOOTH BUDDY", message: "Starting your local workspace…")
                    } else if case .loading = phase {
                        LoadingView(title: "BOOTH BUDDY", message: "Loading your local workspace…")
                    } else if case let .failed(message) = phase {
                        FailureView(message: message, retry: controller.retry, openDefault: defaultWorkspaceAction)
                    } else {
                        NativeSectionView(section: selectedSection, controller: controller) {
                            showDetailedWorkspace = true
                        }
                    }
                }
            }
            .id(refreshRevision)
            .frame(minWidth: 630, minHeight: 560)
        }
        .environmentObject(drafts)
        .onChange(of: controller.workspaceSelectionRevision) { _ in resetWorkspaceDrafts() }
    }

    private var defaultWorkspaceAction: (() -> Void)? {
        guard controller.canOpenDefaultWorkspace else { return nil }
        return { controller.openDefaultWorkspace() }
    }

    private func resetWorkspaceDrafts() {
        drafts.rootPath = ""; drafts.scanMessage = ""; drafts.isScanning = false
        drafts.planResult = nil; drafts.planMessage = ""; drafts.isPlanning = false
        drafts.query = ""; drafts.cleanOnly = false; drafts.plannerQuery = ""
        let defaults = WorkspaceDrafts()
        drafts.eventJSON = defaults.eventJSON; drafts.policyJSON = defaults.policyJSON
        drafts.plannerLimit = defaults.plannerLimit; drafts.plannedCrateName = defaults.plannedCrateName
        selectedSection = "library"; showDetailedWorkspace = false; refreshRevision += 1
    }

    private var isFailure: Bool {
        if case .failed = phase { return true }
        return false
    }

    private var sectionTitle: String {
        switch selectedSection {
        case "planning": return "Set planner"
        case "crates": return "Crates"
        case "operations": return "History & health"
        case "readiness": return "Readiness"
        default: return "Library"
        }
    }
}

struct NativeDetailedWorkspaceView: View {
    let section: String
    let controller: BackendController

    var body: some View {
        NativeOverviewLayout {
            switch section {
            case "planning":
                PlannerDetailEditor(controller: controller)
            case "crates":
                CratesDetailEditor(controller: controller)
            case "operations":
                OperationsDetailEditor(controller: controller)
            case "readiness":
                ReadinessDetailEditor(controller: controller)
            default:
                LibraryDetailEditor(controller: controller)
            }
        }
    }
}

struct CratesDetailEditor: View {
    let controller: BackendController
    var body: some View {
        NativePageHeader(eyebrow: "Native crate workspace", title: "Review every durable change.",
                         detail: "Edit current app-owned crates, reconcile interrupted operations, and verify exports before the gig.")
        NativeApprovalRecoveryView(controller: controller)
        NativeEditRecoveryView(controller: controller)
        NativeInboxReviewRecoveryView(controller: controller)
        NativeSavedPlansView(controller: controller)
        NativeExportHistoryView(controller: controller)
        NativeCrateManager(controller: controller)
    }
}

struct PlannerDetailEditor: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts

    var body: some View {
        NativePageHeader(
            eyebrow: "Native planner workspace",
            title: "Shape the room with evidence.",
            detail: "Generate alternatives from the existing set-planner service, then inspect the result before pinning or repairing anything."
        )
        PlannerBuilderCard(
            controller: controller,
            eventJSON: $drafts.eventJSON,
            policyJSON: $drafts.policyJSON,
            planMessage: $drafts.planMessage,
            planResult: $drafts.planResult,
            isPlanning: $drafts.isPlanning,
            openDetailedWorkspace: nil
        )
        NativeDetailBoundaryNote(
            title: "No automatic crate changes",
            detail: "Planning remains advisory until a human reviews the exact alternatives and approves a durable operation.",
            systemImage: "person.fill.checkmark"
        )
    }
}

struct LibraryDetailEditor: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts

    var body: some View {
        NativePageHeader(
            eyebrow: "Native library workspace",
            title: "Search the index without touching audio.",
            detail: "Use the existing local catalog service to scan and inspect app-owned evidence. Source files and external DJ libraries remain outside the write boundary."
        )
        LibraryScanCard(
            controller: controller,
            rootPath: $drafts.rootPath,
            scanMessage: $drafts.scanMessage,
            isScanning: $drafts.isScanning,
            openDetailedWorkspace: nil
        )
        NativeApprovalRecoveryView(controller: controller)
        NativeEditRecoveryView(controller: controller)
        NativeInboxReviewRecoveryView(controller: controller)
        NativeLibraryIndexView(controller: controller)
        CatalogSearchView(controller: controller)
        NativeDetailBoundaryNote(
            title: "Synthetic-data safe",
            detail: "Use a temporary folder while testing this flow. BOOTH BUDDY never needs to copy or modify source audio.",
            systemImage: "lock.shield"
        )
    }
}

struct OperationsDetailEditor: View {
    let controller: BackendController

    var body: some View {
        NativePageHeader(
            eyebrow: "Native history workspace",
            title: "Understand the trail.",
            detail: "The next native operations surface will expose preview, approval, undo, and recovery receipts without changing their existing API contracts."
        )
        NativeWorkspaceRecoveryView(controller: controller)
        EvidenceSummaryPanel(controller: controller, mode: .health)
        NativeDetailBoundaryNote(
            title: "Recovery remains durable",
            detail: "History removal and file-repair flows continue to require exact reviewed identities and receipts.",
            systemImage: "arrow.triangle.2.circlepath"
        )
    }
}

struct ReadinessDetailEditor: View {
    let controller: BackendController

    var body: some View {
        NativePageHeader(
            eyebrow: "Native readiness workspace",
            title: "Make the boundary visible.",
            detail: "Review local capabilities and release gates before relying on the workspace for a live booth."
        )
        EvidenceSummaryPanel(controller: controller, mode: .capabilities)
        ReadinessChecklist()
        NativeSupportView(controller: controller)
        NativeDetailBoundaryNote(
            title: "Commercial readiness is explicit",
            detail: "Release gates come from the existing capability report; this UI does not infer readiness from a successful launch alone.",
            systemImage: "checkmark.shield"
        )
    }
}

struct NativeDetailBoundaryNote: View {
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .foregroundStyle(BoothBrand.teal)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct NativeSectionView: View {
    let section: String
    let controller: BackendController
    let openDetailedWorkspace: () -> Void

    @ViewBuilder
    var body: some View {
        switch section {
        case "planning":
            PlannerOverview(controller: controller, openDetailedWorkspace: openDetailedWorkspace)
        case "crates":
            CratesOverview(controller: controller, openDetailedWorkspace: openDetailedWorkspace)
        case "operations":
            OperationsOverview(controller: controller, openDetailedWorkspace: openDetailedWorkspace)
        case "readiness":
            ReadinessOverview(controller: controller, openDetailedWorkspace: openDetailedWorkspace)
        default:
            LibraryOverview(controller: controller, openDetailedWorkspace: openDetailedWorkspace)
        }
    }
}

struct NativePageHeader: View {
    let eyebrow: String
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(eyebrow.uppercased())
                .font(.caption.weight(.semibold))
                .tracking(1.2)
                .foregroundStyle(BoothBrand.teal)
            Text(title)
                .font(.system(size: 32, weight: .bold, design: .rounded))
            Text(detail)
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct NativeMetricCard: View {
    let title: String
    let value: String
    let detail: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundStyle(BoothBrand.teal)
            Text(value)
                .font(.title2.weight(.bold))
            Text(title)
                .font(.subheadline.weight(.medium))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct NativeActionCard: View {
    let title: String
    let detail: String
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: systemImage)
                    .font(.title2)
                    .foregroundStyle(BoothBrand.teal)
                    .frame(width: 34)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text(detail)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.leading)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.tertiary)
            }
            .padding(16)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityHint("Opens the detailed workspace")
    }
}

struct NativeOverviewLayout<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                content
            }
            .padding(32)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .accessibilityIdentifier("workspace-scroll")
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct LibraryOverview: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts
    let openDetailedWorkspace: () -> Void

    var body: some View {
        NativeOverviewLayout {
            NativePageHeader(
                eyebrow: "Library",
                title: "Your music, ready for the room.",
                detail: "Index a selected folder locally, review the evidence, and shape it into dependable crates."
            )
            EvidenceSummaryPanel(controller: controller, mode: .library)
            LibraryScanCard(controller: controller, rootPath: $drafts.rootPath,
                            scanMessage: $drafts.scanMessage, isScanning: $drafts.isScanning,
                            openDetailedWorkspace: openDetailedWorkspace)
            NativeActionCard(
                title: "Search indexed music",
                detail: "Find titles, artists, versions and genres in your local index.",
                systemImage: "line.3.horizontal.decrease.circle",
                action: openDetailedWorkspace
            )
        }
    }
}

struct LibraryScanCard: View {
    let controller: BackendController
    @Binding var rootPath: String
    @Binding var scanMessage: String
    @Binding var isScanning: Bool
    let openDetailedWorkspace: (() -> Void)?

    private var validPath: Bool { rootPath.hasPrefix("/") }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Read-only library scan", systemImage: "folder.badge.plus")
                .font(.headline)
            Text("Choose one folder. BOOTH BUDDY indexes evidence locally and never copies or edits audio.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            HStack(spacing: 10) {
                TextField("Absolute music folder path", text: $rootPath)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Absolute music folder path")
                    .accessibilityIdentifier("library-folder-path")
                    .disabled(isScanning)
                Button("Choose…", action: chooseFolder)
                    .disabled(isScanning)
            }
            HStack {
                Button {
                    isScanning = true
                    scanMessage = "Starting scan…"
                    controller.scan(path: rootPath) { result in
                        isScanning = false
                        switch result {
                        case let .success(message):
                            scanMessage = message
                        case let .failure(error):
                            scanMessage = error.localizedDescription
                        }
                    }
                } label: {
                    Label(isScanning ? "Starting…" : "Start scan", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent).tint(BoothBrand.buttonFill).foregroundStyle(.white)
                .accessibilityIdentifier("library-start-scan")
                .disabled(!validPath || isScanning)
                if let openDetailedWorkspace {
                    Button("Open detailed editor", action: openDetailedWorkspace)
                        .buttonStyle(.bordered)
                }
            }
            if !rootPath.isEmpty && !validPath {
                Text("Choose a folder or enter an absolute path starting with /.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !scanMessage.isEmpty {
                Text(scanMessage)
                    .font(.caption)
                    .foregroundStyle(scanMessage.hasPrefix("Scan started") || isScanning ? Color.secondary : Color.red)
                    .accessibilityAddTraits(.updatesFrequently)
            }
            ScanProgressView(controller: controller, trigger: scanMessage)
        }
        .padding(18)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose music folder"
        guard let window = NSApp.keyWindow else { return }
        panel.beginSheetModal(for: window) { response in
            if response == .OK, let url = panel.url { rootPath = url.path }
        }
    }
}

struct PlannerOverview: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts
    let openDetailedWorkspace: () -> Void

    var body: some View {
        NativeOverviewLayout {
            NativePageHeader(
                eyebrow: "Set planner",
                title: "Shape the night with intent.",
                detail: "Turn a booking brief into reviewed, constrained alternatives without changing your library."
            )
            HStack(spacing: 14) {
                NativeMetricCard(title: "Current draft", value: drafts.isPlanning ? "Generating" : (drafts.planResult == nil ? "Draft" : "Generated"), detail: "Inputs stay with you as you navigate", systemImage: "doc.badge.plus")
                NativeMetricCard(title: "Safety model", value: "Reviewed", detail: "Full-track timing by default", systemImage: "checkmark.shield")
            }
            PlannerBuilderCard(controller: controller, eventJSON: $drafts.eventJSON,
                                policyJSON: $drafts.policyJSON, planMessage: $drafts.planMessage,
                                planResult: $drafts.planResult, isPlanning: $drafts.isPlanning,
                                openDetailedWorkspace: openDetailedWorkspace)
            NativeActionCard(
                title: "Review planner inputs",
                detail: "Inspect event requests and planner constraints in the detailed editor.",
                systemImage: "sparkles",
                action: openDetailedWorkspace
            )
        }
    }
}

struct PlannerBuilderCard: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts
    @Binding var eventJSON: String
    @Binding var policyJSON: String
    @Binding var planMessage: String
    @Binding var planResult: PlanResult?
    @Binding var isPlanning: Bool
    let openDetailedWorkspace: (() -> Void)?
    @State private var showAdvanced = false
    @State private var proposal: NativePlanProposal?
    @State private var previewBusy = false
    @State private var previewError = ""
    @StateObject private var audition = NativeAuditionPlayer()

    private var validInputs: Bool {
        [eventJSON, policyJSON].allSatisfy { value in
            guard let data = value.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) else { return false }
            return object is [String: Any]
        }
    }

    private func policyNumber(_ key: String, fallback: Double) -> Binding<Double> {
        Binding(get: {
            guard let data = policyJSON.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return fallback }
            return (json[key] as? NSNumber)?.doubleValue ?? fallback
        }, set: { setPolicy(key, value: $0) })
    }

    private func setPolicy(_ key: String, value: Any) {
        guard let data = policyJSON.data(using: .utf8),
              var json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        json[key] = value
        guard let encoded = try? JSONSerialization.data(withJSONObject: json, options: [.sortedKeys]),
              let text = String(data: encoded, encoding: .utf8) else { return }
        policyJSON = text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Constrained plan draft", systemImage: "calendar.badge.clock").font(.headline)
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Target minutes").font(.caption)
                    TextField("Target minutes", value: policyNumber("target_minutes", fallback: 30), format: .number)
                        .textFieldStyle(.roundedBorder).accessibilityIdentifier("planner-target-minutes")
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("Tolerance minutes").font(.caption)
                    TextField("Tolerance minutes", value: policyNumber("tolerance_minutes", fallback: 3), format: .number)
                        .textFieldStyle(.roundedBorder).accessibilityIdentifier("planner-tolerance-minutes")
                }
            }
            .disabled(!validInputs || isPlanning)
            TextField("Candidate search (title, artist, version or genre)", text: $drafts.plannerQuery)
                .textFieldStyle(.roundedBorder).accessibilityIdentifier("planner-candidate-search")
            Picker("Candidate limit", selection: $drafts.plannerLimit) {
                ForEach([100, 250, 500, 1000], id: \.self) { Text("Up to \($0) matching files").tag($0) }
            }
            .accessibilityIdentifier("planner-candidate-limit")
            Text("Every matching file must fit within this scope. Larger searches are refused until narrowed. Timing uses full-file durations; tagged clean is not listening certification.")
                .font(.caption).foregroundStyle(.secondary)
            Toggle("Require tagged-clean versions", isOn: Binding(get: {
                guard let data = policyJSON.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
                return json["clean_only"] as? Bool ?? false
            }, set: { setPolicy("clean_only", value: $0) }))
            .disabled(!validInputs || isPlanning)
            NativeRequestIntakeView(eventJSON: $eventJSON).disabled(isPlanning)
            NativePlanningDetails(eventJSON: $eventJSON, policyJSON: $policyJSON)
                .disabled(isPlanning)
            NativeFileConstraints(controller: controller, policyJSON: $policyJSON).disabled(isPlanning)
            Button(showAdvanced ? "Hide advanced JSON" : "Advanced event and policy JSON") { showAdvanced.toggle() }
                .accessibilityIdentifier("planner-advanced-toggle")
            if showAdvanced {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Edit additional policy fields. Unsupported fields are refused; visual edits preserve unrelated fields.")
                        .font(.caption).foregroundStyle(.secondary)
                    PlannerJSONEditor(title: "Event data", text: $eventJSON)
                    PlannerJSONEditor(title: "Planner policy", text: $policyJSON)
                }
            }
            if !validInputs {
                Text("Event data and planner policy must each be a valid JSON object.").font(.caption).foregroundStyle(.red)
            }
            HStack {
                Button {
                    let submittedEvent = eventJSON, submittedPolicy = policyJSON
                    let query = drafts.plannerQuery, limit = drafts.plannerLimit
                    isPlanning = true; planMessage = "Generating constrained alternatives…"
                    planResult = nil; proposal = nil; previewError = ""
                    controller.generatePlan(event: eventJSON, policy: policyJSON, query: query, limit: limit) { result in
                        isPlanning = false
                        guard eventJSON == submittedEvent, policyJSON == submittedPolicy,
                              drafts.plannerQuery == query, drafts.plannerLimit == limit else {
                            planMessage = "Draft changed. Generate alternatives for the current inputs."
                            return
                        }
                        switch result {
                        case let .success(value):
                            planResult = value
                            switch value.response.diagnostics.status {
                            case "feasible": planMessage = "Alternatives are ready for exact-file review."
                            case "infeasible": planMessage = "The supplied scope and constraints are infeasible. Nothing was relaxed."
                            default: planMessage = "Search exhausted its budget. Feasibility remains unknown; narrow the scope or revise the draft."
                            }
                        case let .failure(error): planMessage = error.localizedDescription
                        }
                    }
                } label: {
                    Label(isPlanning ? "Generating…" : "Generate alternatives", systemImage: "wand.and.stars")
                }
                .buttonStyle(.borderedProminent).tint(BoothBrand.buttonFill).foregroundStyle(.white).accessibilityIdentifier("planner-generate")
                .disabled(isPlanning || !validInputs)
                if let openDetailedWorkspace {
                    Button("Open advanced planner", action: openDetailedWorkspace).buttonStyle(.bordered)
                }
            }
            if !planMessage.isEmpty { Text(planMessage).font(.caption).textSelection(.enabled).accessibilityIdentifier("planner-message") }
            if let result = planResult {
                let diagnostics = result.response.diagnostics
                Text("\(diagnostics.status.replacingOccurrences(of: "_", with: " ").capitalized) · \(diagnostics.eligibleCount) eligible · \(diagnostics.candidateCount) searched")
                    .font(.subheadline.weight(.medium)).accessibilityIdentifier("planner-status")
                if !diagnostics.searchComplete {
                    Text("Search was bounded: \(diagnostics.truncatedBy.joined(separator: ", ")). A returned set satisfies its constraints; global optimality is not guaranteed.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(result.response.unresolved.enumerated()), id: \.offset) { _, issue in
                    Text(issue.diagnosticText).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
                }
                if result.feasible {
                    TextField("Name for the reviewed crate", text: $drafts.plannedCrateName)
                        .textFieldStyle(.roundedBorder).accessibilityIdentifier("planner-crate-name")
                    ForEach(Array(result.response.alternatives.enumerated()), id: \.offset) { index, alternative in
                        NativeAlternativeCard(alternative: alternative, index: index, controller: controller, audition: audition)
                        Button("Preview alternative \(index + 1) for approval") {
                            let name = drafts.plannedCrateName
                            previewBusy = true; proposal = nil; previewError = ""
                            Task {
                                defer { previewBusy = false }
                                do {
                                    let value = try await controller.previewNativePlan(result, index: index, name: name)
                                    guard planResult == result, name == drafts.plannedCrateName else { return }
                                    proposal = value
                                } catch { previewError = error.localizedDescription }
                            }
                        }
                        .accessibilityIdentifier("plan-preview-\(index)")
                        .disabled(previewBusy || drafts.plannedCrateName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
            if previewBusy { ProgressView("Preparing exact-file proposal…") }
            if !previewError.isEmpty { Text(previewError).font(.caption).foregroundStyle(.red) }
            if let proposal { NativePlanApprovalView(controller: controller, proposal: proposal) }
            NativeApprovalRecoveryView(controller: controller)
            NativeEditRecoveryView(controller: controller)
            NativeInboxReviewRecoveryView(controller: controller)
            NativeSavedPlansView(controller: controller)
        }
        .onChange(of: eventJSON) { _ in invalidate() }
        .onChange(of: policyJSON) { _ in invalidate() }
        .onChange(of: drafts.plannerQuery) { _ in invalidate() }
        .onChange(of: drafts.plannerLimit) { _ in invalidate() }
        .onChange(of: drafts.plannedCrateName) { _ in proposal = nil }
        .onDisappear { audition.stop() }
        .padding(18)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }

    private func invalidate() { audition.stop(); planResult = nil; planMessage = ""; proposal = nil; previewError = "" }
}

struct NativeAlternativeCard: View {
    let alternative: NativePlanAlternative
    let index: Int
    let controller: BackendController
    @ObservedObject var audition: NativeAuditionPlayer

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Alternative \(index + 1) · \(alternative.tracks.count) tracks · \(nativeDuration(alternative.durationS))")
                .font(.headline).accessibilityIdentifier("plan-alternative-\(index)")
            Text(String(format: "Target delta %+.1f seconds · score %.2f", alternative.deltaS, alternative.score))
                .font(.caption).foregroundStyle(.secondary)
            ForEach(Array((alternative.phases ?? []).enumerated()), id: \.offset) { _, phase in
                Text("\(phase.label): positions \(phase.startPosition + 1)–\(phase.endPosition), \(nativeDuration(phase.durationS))")
                    .font(.caption.weight(.medium))
            }
            ForEach(Array(alternative.tracks.enumerated()), id: \.offset) { position, track in
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(position + 1). \(track.title) — \(track.artist)").font(.subheadline.weight(.semibold))
                    Text(track.path).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled).help(track.path)
                    ForEach(alternative.trackReasons.first(where: { $0.fileId == track.id })?.reasons ?? [], id: \.self) { reason in
                        Text(reason).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
            }
            Text("Full-file duration evidence only. This is not a measured performance or a certified transition.")
                .font(.caption).foregroundStyle(.secondary)
            NativeTransitionReview(alternative: alternative, index: index, controller: controller, audition: audition)
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct NativeTransitionReview: View {
    let alternative: NativePlanAlternative
    let index: Int
    let controller: BackendController
    @ObservedObject var audition: NativeAuditionPlayer
    @State private var expanded = false
    @State private var position = 0.0
    private func name(_ id: String) -> String { alternative.tracks.first(where: { $0.id == id }).map { "\($0.title) — \($0.artist)" } ?? "Unknown file \(id)" }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button(expanded ? "Hide transition review and listening" : "Review transitions and listen") { expanded.toggle() }
                .accessibilityIdentifier("transition-review-\(index)")
            if expanded {
                if let report = alternative.transitionEvidence, report.schemaVersion == 1 {
                    Text("Timing evidence: \(report.status)").font(.headline).accessibilityIdentifier("transition-status-\(index)")
                    if let duration = report.fullFileRuntimeS { Text("Full-file runtime: \(nativeDuration(duration))").font(.caption) }
                    if report.status == "estimated", let elapsed = report.estimatedElapsedS { Text("Reviewed-window estimate: \(nativeDuration(elapsed)). Not measured playback.").font(.caption) }
                    else { Text("No complete mixed-runtime estimate. Review missing windows and overlaps before relying on mixed timing.").font(.caption).foregroundStyle(.orange) }
                    ForEach(Array(report.issues.enumerated()), id: \.offset) { _, issue in Text(issue.diagnosticText).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
                    ForEach(Array(report.transitions.enumerated()), id: \.offset) { pairIndex, pair in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(pairIndex + 1). \(name(pair.fromId)) → \(name(pair.toId))").font(.caption.weight(.semibold))
                            Text("BPM: \(pair.bpm.map { $0.map { String(format: "%.1f", $0) } ?? "unknown" }.joined(separator: " → ")) · Keys: \(pair.camelotKeys.map { $0 ?? "unknown" }.joined(separator: " → "))").font(.caption)
                            if let tempo = pair.tempoChangePercent { Text(String(format: "Tagged tempo change: %+.1f%%", tempo)).font(.caption) }
                            Text(pair.overlapS.map { "Explicit overlap: \($0) seconds" } ?? "Explicit overlap: missing").font(.caption)
                            ForEach(pair.warnings, id: \.self) { warning in Text(warning.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(.orange) }
                            Text(pair.assumptions).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    Text(report.note).font(.caption2).foregroundStyle(.secondary)
                } else { Text("Transition timing evidence is unavailable for this alternative.").font(.caption).foregroundStyle(.orange) }
                Text("Single-file listening from the selected plan, one player at a time. Audio is verified in memory (up to 64 MiB); nothing is written or uploaded. Listening does not review cue windows, infer an overlap or approve a transition.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(Array(alternative.tracks.enumerated()), id: \.offset) { trackIndex, track in
                    HStack {
                        Text("\(trackIndex + 1). \(track.title) — \(track.artist)").font(.caption)
                        Spacer()
                        Button("Listen to file \(trackIndex + 1)") { audition.play(track, controller: controller, ownerIndex: index) }
                            .accessibilityIdentifier("audition-play-\(index)-\(trackIndex)")
                    }
                }
                if audition.ownerIndex == index, let active = audition.activeTrackId, alternative.tracks.contains(where: { $0.id == active }) {
                    VStack(alignment: .leading, spacing: 8) {
                        if audition.loading { ProgressView("Verifying audio…") }
                        Text(audition.message).font(.caption).textSelection(.enabled).accessibilityIdentifier("audition-status-\(index)")
                        if audition.duration > 0 {
                            Slider(value: Binding(get: { position }, set: { position = $0; audition.seek($0) }), in: 0...max(1, audition.duration))
                                .accessibilityLabel("Audition position").accessibilityIdentifier("audition-position-\(index)")
                            Text("\(nativeDuration(position)) / \(nativeDuration(audition.duration))").font(.caption2)
                        }
                        HStack {
                            Button(audition.playing ? "Pause audition" : "Resume audition") { audition.togglePause() }
                                .disabled(audition.loading || audition.duration == 0).accessibilityIdentifier("audition-pause-\(index)")
                            Button("Stop audition") { audition.stop() }.accessibilityIdentifier("audition-stop-\(index)")
                        }
                    }
                    .onReceive(Timer.publish(every: 0.25, on: .main, in: .common).autoconnect()) { _ in position = audition.currentTime }
                }
            }
        }
        .onChange(of: expanded) { value in if !value, audition.ownerIndex == index { audition.stop() } }
        .onDisappear { if audition.ownerIndex == index { audition.stop() } }
    }
}

struct PlannerJSONEditor: View {
    let title: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption.weight(.semibold))
            TextEditor(text: $text)
                .accessibilityLabel(title)
                .accessibilityIdentifier(title == "Event data" ? "planner-event-json" : "planner-policy-json")
                .font(.system(.body, design: .monospaced))
                .frame(minHeight: 120)
                .padding(6)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
        }
        .frame(maxWidth: .infinity)
    }
}

struct LiveSummaryPanel: View {
    let controller: BackendController
    @State private var summary: WorkspaceSummary?
    @State private var message = ""

    var body: some View {
        Group {
            if let summary {
                HStack(spacing: 14) {
                    NativeMetricCard(title: "Smart rules", value: "\(summary.rules)", detail: "Saved local recipes", systemImage: "line.3.horizontal.decrease.circle")
                    NativeMetricCard(title: "Personal crates", value: "\(summary.crates)", detail: "App-owned crates", systemImage: "shippingbox")
                    NativeMetricCard(title: "Play history", value: "\(summary.history)", detail: "Recorded in this app", systemImage: "clock.arrow.circlepath")
                }
            } else if !message.isEmpty {
                NativeEmptyState(title: "Workspace state unavailable", detail: message, systemImage: "wifi.exclamationmark")
            } else {
                ProgressView("Loading workspace state…")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task {
            controller.loadSummary { result in
                switch result {
                case let .success(value):
                    summary = value
                case let .failure(error):
                    message = error.localizedDescription
                }
            }
        }
    }
}

struct CratesOverview: View {
    let controller: BackendController
    let openDetailedWorkspace: () -> Void

    var body: some View {
        NativeOverviewLayout {
            NativePageHeader(
                eyebrow: "Crates",
                title: "Keep every crate intentional.",
                detail: "Review smart rules, static personal crates, exports, and recovery evidence in one place."
            )
            LiveSummaryPanel(controller: controller)
            NativeActionCard(
                title: "Review saved crates",
                detail: "Inspect the exact files and evidence behind each personal crate.",
                systemImage: "shippingbox",
                action: openDetailedWorkspace
            )
            NativeActionCard(
                title: "Preview a crate rename",
                detail: "Inspect the current revision and review a proposed name before any approval.",
                systemImage: "square.stack.3d.up",
                action: openDetailedWorkspace
            )
            NativeEmptyState(
                title: "No crate changes are made automatically",
                detail: "BOOTH BUDDY keeps source audio and external DJ crates unchanged.",
                systemImage: "hand.raised.fill"
            )
        }
    }
}

struct OperationsOverview: View {
    let controller: BackendController
    let openDetailedWorkspace: () -> Void

    var body: some View {
        NativeOverviewLayout {
            NativePageHeader(
                eyebrow: "History & health",
                title: "Know what changed.",
                detail: "See library health, play history, recovery receipts, and exact-hash repair evidence."
            )
            LiveSummaryPanel(controller: controller)
            EvidenceSummaryPanel(controller: controller, mode: .health)
            NativeActionCard(
                title: "Inspect history and health",
                detail: "Inspect indexed counts, missing-file evidence and duplicate hash groups.",
                systemImage: "clock.arrow.circlepath",
                action: openDetailedWorkspace
            )
            NativeActionCard(
                title: "Review operation boundaries",
                detail: "See the evidence limits before making another library change.",
                systemImage: "arrow.triangle.2.circlepath",
                action: openDetailedWorkspace
            )
        }
    }
}

struct ReadinessOverview: View {
    let controller: BackendController
    let openDetailedWorkspace: () -> Void

    var body: some View {
        NativeOverviewLayout {
            NativePageHeader(
                eyebrow: "Readiness",
                title: "A calmer booth starts here.",
                detail: "Check the local workspace, review release capabilities, and keep the final call with the DJ."
            )
            LiveSummaryPanel(controller: controller)
            ReadinessChecklist()
            NativeActionCard(
                title: "Open release readiness",
                detail: "Inspect the current capability report and detailed evidence.",
                systemImage: "checkmark.shield",
                action: openDetailedWorkspace
            )
        }
    }
}

struct ReadinessChecklist: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ReadinessRow(title: "Local workspace", detail: "Running on this Mac", systemImage: "checkmark.circle.fill", color: .green)
            ReadinessRow(title: "Source audio", detail: "Never copied or modified", systemImage: "checkmark.circle.fill", color: .green)
            ReadinessRow(title: "Human review", detail: "Required before durable changes", systemImage: "person.fill.checkmark", color: .orange)
        }
        .padding(18)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct ReadinessRow: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
        }
    }
}

struct NativeEmptyState: View {
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: systemImage)
                .font(.title2)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct WorkspaceHeader: View {
    let title: String
    let phase: WorkspacePhase
    let reload: () -> Void
    @Binding var showDetailedWorkspace: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.title3.weight(.semibold))
                Text("BOOTH BUDDY · Local workspace")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            StatusBadge(phase: phase)
            Button {
                showDetailedWorkspace.toggle()
            } label: {
                Label(showDetailedWorkspace ? "Overview" : "Detailed workspace",
                      systemImage: showDetailedWorkspace ? "rectangle.grid.1x2" : "slider.horizontal.3")
            }
            .help(showDetailedWorkspace ? "Show the native overview" : "Open the full workspace editor")
            .accessibilityIdentifier("detailed-workspace-toggle")
            .keyboardShortcut("d", modifiers: [.command, .shift])
            .accessibilityValue(showDetailedWorkspace ? "Detailed workspace" : "Overview")
            .disabled(!isReady)
            .opacity(isReady ? 1 : 0.55)
            Button(action: reload) {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .labelStyle(.iconOnly)
            .help("Reload this workspace")
            .accessibilityLabel("Refresh workspace")
            .keyboardShortcut("r", modifiers: .command)
            .disabled(!isReady)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private var isReady: Bool {
        if case .ready = phase { return true }
        return false
    }
}

struct StatusBadge: View {
    let phase: WorkspacePhase

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 7, height: 7)
            Text(label)
        }
        .font(.caption.weight(.medium))
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(label)
    }

    private var label: String {
        switch phase {
        case .starting: return "Starting"
        case .loading: return "Loading"
        case .ready: return "On this Mac"
        case .failed: return "Needs attention"
        }
    }

    private var color: Color {
        switch phase {
        case .starting, .loading: return .orange
        case .ready: return BoothBrand.teal
        case .failed: return .red
        }
    }
}

struct BoothBuddySidebar: View {
    @Binding var selectedSection: String
    let onSelect: (String) -> Void
    private let sections = [
        ("library", "Library", "music.note.list"),
        ("planning", "Set planner", "calendar.badge.clock"),
        ("crates", "Crates", "shippingbox"),
        ("operations", "History & health", "clock.arrow.circlepath"),
        ("readiness", "Readiness", "checkmark.shield")
    ]

    var body: some View {
        let brandTeal = BoothBrand.teal

        return ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("BOOTH BUDDY")
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                        .foregroundStyle(brandTeal)
                    Text("Your local DJ workspace")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 16)
                .padding(.top, 20)
                .padding(.bottom, 18)

                Text("WORKSPACE")
                    .font(.caption2.weight(.bold))
                    .tracking(1.4)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)

                ForEach(sections, id: \.0) { section in
                    Button {
                        selectedSection = section.0
                        onSelect(section.0)
                    } label: {
                        Label {
                            Text(section.1)
                                .font(.subheadline.weight(selectedSection == section.0 ? .semibold : .regular))
                        } icon: {
                            Image(systemName: section.2)
                                .font(.body.weight(.semibold))
                                .frame(width: 22)
                        }
                        .foregroundStyle(selectedSection == section.0 ? brandTeal : Color.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(
                            selectedSection == section.0
                                ? brandTeal.opacity(0.16)
                                : Color.clear,
                            in: RoundedRectangle(cornerRadius: 9)
                        )
                    }
                    .buttonStyle(.plain)
                    .padding(.horizontal, 10)
                    .accessibilityIdentifier("sidebar-\(section.0)")
                    .accessibilityAddTraits(selectedSection == section.0 ? .isSelected : [])
                }

                Spacer(minLength: 28)

                HStack(spacing: 8) {
                    Circle()
                        .fill(brandTeal)
                        .frame(width: 8, height: 8)
                    Text("On this Mac")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 18)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .frame(minWidth: 230, idealWidth: 250, maxWidth: 300)
    }
}

struct LoadingView: View {
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 16) {
            Text(title).font(.largeTitle.weight(.bold)).foregroundStyle(BoothBrand.teal)
            ProgressView()
            Text(message).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct FailureView: View {
    let message: String
    let retry: () -> Void
    var openDefault: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle").font(.largeTitle).foregroundStyle(.orange)
            Text("BOOTH BUDDY could not start").font(.title2.weight(.semibold))
            Text(message).multilineTextAlignment(.center).foregroundStyle(.secondary)
            HStack {
                Button("Retry", action: retry).keyboardShortcut(.defaultAction)
                if let openDefault { Button("Open default workspace", action: openDefault) }
                Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
            }
        }
        .padding(32)
        .frame(maxWidth: 520)
    }
}

@MainActor
final class BoothBuddyAppDelegate: NSObject, NSApplicationDelegate {
    var onTerminate: (() -> Void)?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { onTerminate?() }
}

@main
struct BoothBuddyApp: App {
    @NSApplicationDelegateAdaptor(BoothBuddyAppDelegate.self) private var appDelegate
    @State private var phase: WorkspacePhase = .starting
    @StateObject private var controller = BackendController()
    private var testAppearance: ColorScheme? {
        let arguments = ProcessInfo.processInfo.arguments
        return arguments.contains("--ui-testing") && arguments.contains("--synthetic-data") && arguments.contains("--test-light-appearance") ? .light : nil
    }

    var body: some Scene {
        WindowGroup("BOOTH BUDDY") {
            BoothBuddyRootView(phase: $phase, controller: controller)
                .preferredColorScheme(testAppearance)
                .tint(BoothBrand.teal)
                .task {
                    appDelegate.onTerminate = { controller.stop() }
                    controller.onPhaseChange = { newPhase in
                        phase = newPhase
                    }
                    controller.start()
                }
        }
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("About BOOTH BUDDY") {
                    NSApp.orderFrontStandardAboutPanel(nil)
                }
            }
            CommandGroup(replacing: .help) {
                Button("Save redacted support report…") {
                    Task { @MainActor in
                        do { _ = try await controller.saveRedactedNativeSupportReport() }
                        catch {
                            let alert = NSAlert()
                            alert.messageText = "Support report could not be saved"
                            alert.informativeText = error.localizedDescription
                            alert.runModal()
                        }
                    }
                }
            }
        }
    }
}

struct CatalogSearchView: View {
    let controller: BackendController
    @EnvironmentObject private var drafts: WorkspaceDrafts
    @State private var tracks: [CatalogTrack] = []
    @State private var total = 0
    @State private var loading = false
    @State private var message = ""
    @State private var attempt = 0
    @FocusState private var searchFocused: Bool

    private var searchKey: String { "\(drafts.query)\u{0}\(drafts.cleanOnly)\u{0}\(attempt)" }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Search indexed music", systemImage: "magnifyingglass").font(.headline)
            HStack {
                TextField("Title, artist, version or genre", text: $drafts.query)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Search indexed music")
                    .accessibilityIdentifier("catalog-search")
                    .focused($searchFocused)
                Button { searchFocused = true } label: { Image(systemName: "magnifyingglass") }
                    .keyboardShortcut("f", modifiers: .command)
                    .accessibilityLabel("Focus catalog search")
                    .help("Search indexed music (⌘F)")
                if !drafts.query.isEmpty {
                    Button("Clear") { drafts.query = "" }.accessibilityIdentifier("catalog-clear-search")
                }
            }
            Toggle("Tagged clean only", isOn: $drafts.cleanOnly)
                .accessibilityIdentifier("catalog-clean-only")
            Text("Search requires title and artist tags. Indexed metadata only; tagged clean is not a listening certification. Availability is checked again on approval or export.")
                .font(.caption).foregroundStyle(.secondary)
            if loading {
                ProgressView("Searching the index…")
            } else if !message.isEmpty {
                Text(message).foregroundStyle(.red).font(.caption)
                Button("Retry search") { attempt += 1 }
            } else {
                Text("\(total) matching \(total == 1 ? "track" : "tracks")\(total > tracks.count ? " · showing first \(tracks.count)" : "")")
                    .font(.caption.weight(.medium))
                    .accessibilityIdentifier("catalog-result-count")
                if tracks.isEmpty {
                    Text(drafts.query.isEmpty && !drafts.cleanOnly
                         ? "No searchable tracks yet. Scan files with title and artist tags to begin."
                         : "No matching tracks. Try another search or turn off the clean filter.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(tracks) { track in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(track.title).font(.subheadline.weight(.semibold))
                            Text(track.artist).font(.caption).foregroundStyle(.secondary)
                            Text(track.metadata).font(.caption).foregroundStyle(.secondary)
                            Text(track.path).font(.caption2).foregroundStyle(.secondary)
                                .lineLimit(2).textSelection(.enabled).help(track.path)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
        .padding(18)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
        .task(id: searchKey) {
            loading = true
            message = ""
            do {
                try await Task.sleep(nanoseconds: 250_000_000)
                let result = try await controller.searchCatalog(query: drafts.query, cleanOnly: drafts.cleanOnly)
                try Task.checkCancellation()
                tracks = result.tracks
                total = result.total
                loading = false
            } catch is CancellationError {
                // The replacement task owns loading and results.
            } catch {
                guard !Task.isCancelled else { return }
                loading = false
                message = error.localizedDescription
            }
        }
    }
}

struct NativeLibraryIndexView: View {
    @ObservedObject var controller: BackendController
    @State private var expanded = false
    @State private var roots: [NativeIndexedRoot] = []
    @State private var inbox: NativeLibraryInbox?
    @State private var filter = "all"
    @State private var query = ""
    @State private var selectedRoot = ""
    @State private var attempt = 0
    @State private var readId = UUID()
    @State private var loading = false
    @State private var message = ""
    @State private var checkedAt: Date?
    @State private var reviewFile: NativeInboxFile?
    private var visible: [NativeInboxFile] { inbox?.visibleItems(view: filter, query: query, rootId: selectedRoot) ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(expanded ? "Hide indexed folders and ingestion records" : "Review indexed folders and ingestion records") { expanded.toggle() }
                .accessibilityIdentifier("library-index-toggle")
            if expanded {
                Text("Folder identity is checked on refresh. File availability, metadata and same-hash groups below are published index evidence, not a fresh source-byte verification. Viewing evidence changes no files or tags. Inbox status changes require a separate explicit review.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Refresh index evidence") { attempt += 1 }.disabled(loading).accessibilityIdentifier("index-refresh")
                if loading { ProgressView("Loading index evidence…") }
                if let checkedAt { Text("Last checked: \(checkedAt.formatted(date: .abbreviated, time: .shortened))").font(.caption2).foregroundStyle(.secondary) }
                ForEach(roots) { root in rootCard(root) }
                if inbox != nil && roots.isEmpty { Text("No indexed folders. Choose a test folder above and scan it first.").font(.caption) }
                if let inbox {
                    Text("\(inbox.items.count) loaded · \(inbox.counts.values.reduce(0, +)) ingestion records overall")
                        .font(.subheadline.weight(.semibold)).accessibilityIdentifier("index-loaded-count")
                    Text("The inbox returns up to \(inbox.limit) recent records, not the whole library. These filters apply only to loaded records. Title/artist gaps remain visible here even when catalog search excludes them.")
                        .font(.caption).foregroundStyle(.secondary)
                    Picker("Indexed folder", selection: $selectedRoot) {
                        Text("All indexed folders").tag("")
                        ForEach(roots) { root in Text(root.path).tag(root.id) }
                    }.accessibilityIdentifier("index-root-filter")
                    Picker("File view", selection: $filter) {
                        Text("All loaded records").tag("all"); Text("Needs title or artist").tag("search-tags")
                        Text("Any missing metadata").tag("metadata"); Text("Indexed unavailable").tag("unavailable")
                        Text("Indexed errors").tag("errors")
                        Text("Indexed same-hash groups").tag("duplicates")
                    }.accessibilityIdentifier("index-filter")
                    TextField("Filter loaded titles, artists or paths", text: $query).textFieldStyle(.roundedBorder).accessibilityIdentifier("index-search")
                    Text("\(visible.count) loaded records match this view").font(.caption).accessibilityIdentifier("index-visible-count")
                    if visible.isEmpty { Text("No loaded records match these filters.").font(.caption).foregroundStyle(.secondary) }
                    ForEach(visible) { file in fileCard(file) }
                }
                if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red).textSelection(.enabled).accessibilityIdentifier("index-error") }
            }
        }
        .sheet(item: $reviewFile) { file in NativeInboxReviewConfirmation(controller: controller, file: file) }
        .task(id: "\(expanded)-\(attempt)-\(controller.nativeWorkspaceRevision)") {
            guard expanded else { return }
            let current = UUID(); readId = current; loading = true; message = ""
            defer { if readId == current { loading = false } }
            do {
                async let rootResponse = controller.nativeIndexedRoots()
                async let inboxResponse = controller.nativeLibraryInbox()
                let values = try await (rootResponse, inboxResponse)
                try Task.checkCancellation()
                guard readId == current else { return }
                roots = values.0; inbox = values.1; checkedAt = Date()
            } catch is CancellationError {
            } catch { if !Task.isCancelled, readId == current { message = "Index evidence could not be refreshed: \(error.localizedDescription)" } }
        }
    }
    private func rootCard(_ root: NativeIndexedRoot) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(root.state.capitalized).font(.subheadline.weight(.semibold)).foregroundStyle(root.state == "online" ? BoothBrand.teal : .orange).accessibilityIdentifier("index-root-state")
            Text(root.path).font(.caption).textSelection(.enabled)
            Text("Published scan revision \(root.scanRevision)").font(.caption2).foregroundStyle(.secondary)
            if !root.availabilityError.isEmpty { Text(root.availabilityError).font(.caption).foregroundStyle(.orange) }
        }
    }
    private func fileCard(_ file: NativeInboxFile) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(file.title).font(.subheadline.weight(.semibold)).accessibilityIdentifier("index-file-title")
            Text(file.tag("artist").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Artist tag missing" : file.tag("artist")).font(.caption)
            Text(file.relativePath).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
            Text("Inbox: \(file.status) · revision \(file.revision) · indexed \(file.available == 1 ? "available" : "unavailable")").font(.caption)
            if let root = roots.first(where: { $0.id == file.rootId }), root.state != "online" { Text("Folder was offline at the last check; the indexed record is retained.").font(.caption).foregroundStyle(.orange) }
            if file.needsSearchTags { Text("Title or artist tags need review before this file can appear in catalog search.").font(.caption).foregroundStyle(.orange) }
            if !file.missingMetadata.isEmpty { Text("Missing indexed metadata: \(file.missingMetadata.joined(separator: ", "))").font(.caption).foregroundStyle(.secondary) }
            if !file.error.isEmpty { Text(file.error).font(.caption).foregroundStyle(.orange) }
            if file.exactHashGroupCount > 1 { Text("Indexed same-hash group: \(file.exactHashGroupCount) files. This is not a deletion recommendation.").font(.caption).foregroundStyle(.secondary) }
            Button("Review inbox status…") { reviewFile = file }
                .accessibilityIdentifier("index-review-\(file.fileId)")
                .disabled(controller.approvalBusy || controller.hasPendingNativeOperation || controller.workflowJournal == nil)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct NativeInboxReviewConfirmation: View {
    @ObservedObject var controller: BackendController
    let file: NativeInboxFile
    @Environment(\.dismiss) private var dismiss
    @State private var status = "reviewed"
    @State private var message = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Review inbox status").font(.title2.weight(.semibold))
            Text(file.title).font(.headline)
            Text(file.relativePath).font(.caption).textSelection(.enabled)
            Text("Exact file: \(file.fileId) · inbox revision \(file.revision) · current status: \(file.status)")
                .font(.caption).textSelection(.enabled)
            Text("This records app-owned inbox triage only. Reviewed does not certify tags, cleanliness, musical quality or event suitability. Ignored does not hide a file from catalog search, exclude it from planning, or delete it. A later scan that changes its indexed evidence returns it to New.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("New inbox status", selection: $status) {
                Text("New").tag("new"); Text("Reviewed").tag("reviewed"); Text("Ignored").tag("ignored")
            }.accessibilityIdentifier("inbox-review-status")
            HStack {
                Button("Close") { dismiss() }.disabled(controller.approvalBusy)
                Button("Save inbox status") {
                    message = ""
                    Task {
                        do { try await controller.saveNativeInboxReview(file, status: status); dismiss() }
                        catch { message = error.localizedDescription }
                    }
                }.buttonStyle(.borderedProminent).tint(BoothBrand.teal)
                    .accessibilityIdentifier("inbox-review-save")
                    .disabled(status == file.status || controller.approvalBusy || controller.hasPendingNativeOperation || controller.workflowJournal == nil)
            }
            if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red).accessibilityIdentifier("inbox-review-error") }
        }.padding(24).frame(width: 540)
    }
}

struct NativeInboxReviewRecoveryView: View {
    @ObservedObject var controller: BackendController
    @State private var message = ""
    var body: some View {
        if let pending = controller.workflowJournal?.pendingInboxReview {
            VStack(alignment: .leading, spacing: 8) {
                Text(pending.rejected ? "Inbox status rejected" : "Inbox status needs reconciliation")
                    .font(.headline).accessibilityIdentifier("inbox-review-pending")
                Text("\(pending.title) · \(pending.request.status) · request \(pending.request.requestId)").font(.caption).textSelection(.enabled)
                Text(pending.rejected ? "Discard this rejected request, refresh the evidence and review again." : "The exact status request is retained on this Mac. Retry it to retrieve the same receipt before making another durable change.")
                    .font(.caption).foregroundStyle(.secondary)
                Button(pending.rejected ? "Discard rejected inbox status" : "Retry exact inbox status") {
                    message = ""
                    Task {
                        do {
                            if pending.rejected { try controller.discardRejectedNativeInboxReview() }
                            else { try await controller.retryNativeInboxReview() }
                        } catch { message = error.localizedDescription }
                    }
                }.accessibilityIdentifier("inbox-review-retry").disabled(controller.approvalBusy)
                if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red) }
            }
        }
    }
}

extension BackendController {
    func saveNativeInboxReview(_ file: NativeInboxFile, status: String) async throws {
        guard !approvalBusy, !hasPendingNativeOperation, var journal = workflowJournal, status != file.status else {
            throw nativeWorkflowError("Resolve recovery or choose a changed inbox status before saving.")
        }
        let request = NativeInboxReviewRequest(requestId: UUID().uuidString.lowercased(), datasetId: datasetID,
                                             fileId: file.fileId, baseRevision: file.revision, status: status)
        try request.validate(dataset: datasetID)
        journal.pendingInboxReview = NativePendingInboxReview(request: request, title: file.title, relativePath: file.relativePath)
        try persistWorkflowJournal(journal)
        try await retryNativeInboxReview()
    }
    func retryNativeInboxReview() async throws {
        guard !approvalBusy, let pending = workflowJournal?.pendingInboxReview, !pending.rejected else {
            throw nativeWorkflowError("There is no retryable inbox status change.")
        }
        approvalBusy = true
        defer { approvalBusy = false }
        do {
            let raw = try await nativeBytes("api/studio/inbox/review", bodyData: pending.request.data(dataset: datasetID))
            let receipt = try Self.decodeNative(NativeInboxReviewReceipt.self, raw)
            try receipt.validate(pending.request, dataset: datasetID)
            let args = ProcessInfo.processInfo.arguments
            if args.contains("--ui-testing"), args.contains("--synthetic-data"), args.contains("--test-drop-inbox-ack"), !simulatedInboxAcknowledgementLoss {
                simulatedInboxAcknowledgementLoss = true
                throw nativeWorkflowError("Inbox response interrupted. Close this review and retry the retained request from recovery.")
            }
            guard var journal = workflowJournal, journal.pendingInboxReview?.request == pending.request else {
                throw nativeWorkflowError("Inbox recovery changed unexpectedly. Reopen before continuing.")
            }
            journal.pendingInboxReview = nil
            try persistWorkflowJournal(journal)
            nativeWorkspaceRevision += 1
        } catch {
            let failure = error as NSError
            if failure.domain == "BOOTHBUDDY", [400, 409].contains(failure.code), var journal = workflowJournal {
                journal.pendingInboxReview?.rejected = true
                try persistWorkflowJournal(journal)
            }
            throw error
        }
    }
    func discardRejectedNativeInboxReview() throws {
        guard !approvalBusy, var journal = workflowJournal, journal.pendingInboxReview?.rejected == true else {
            throw nativeWorkflowError("An uncertain inbox status must be reconciled, not discarded.")
        }
        journal.pendingInboxReview = nil
        try persistWorkflowJournal(journal)
        nativeWorkspaceRevision += 1
    }
}

struct ScanProgressView: View {
    let controller: BackendController
    let trigger: String
    @State private var jobs: [ScanJob] = []
    @State private var message = ""
    @State private var retry = 0
    @State private var lastUpdated: Date?
    @State private var cancelingId: String?
    @State private var cancelMessage = ""
    @State private var historyExpanded = false
    private var history: NativeScanHistory { NativeScanHistory(jobs: jobs, expanded: historyExpanded) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !jobs.isEmpty {
                Text("Recent scans").font(.subheadline.weight(.semibold))
                Text("\(history.active.count) active · \(history.inactive.count) inactive in the returned snapshot")
                    .font(.caption).accessibilityIdentifier("scan-history-summary")
                Text("The service returns all active scans and up to 20 recent inactive jobs. Older inactive jobs are outside this snapshot.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            if !history.active.isEmpty { Text("Active scans").font(.caption.weight(.semibold)) }
            ForEach(history.active) { job in scanCard(job) }
            if !history.inactive.isEmpty { Text("Scan history").font(.caption.weight(.semibold)) }
            ForEach(history.visibleHistory) { job in scanCard(job) }
            if history.hiddenCount > 0 || (historyExpanded && history.inactive.count > 5) {
                Button(historyExpanded ? "Show fewer inactive scans" : "Show older scans (\(history.hiddenCount))") { historyExpanded.toggle() }
                    .accessibilityIdentifier("scan-history-toggle")
            }
            if !cancelMessage.isEmpty { Text(cancelMessage).font(.caption).textSelection(.enabled).accessibilityIdentifier("scan-cancel-message") }
            if !message.isEmpty {
                Text(message).font(.caption).foregroundStyle(.red)
                Button("Retry scan status") { retry += 1 }
            } else if let lastUpdated {
                HStack {
                    Text("Updated \(lastUpdated, style: .time)").font(.caption2).foregroundStyle(.secondary)
                    Button("Refresh scan status") { retry += 1 }.font(.caption)
                }
            }
        }
        .task(id: "\(trigger)\u{0}\(retry)") {
            do {
                repeat {
                    jobs = try await controller.scanJobs()
                    message = ""
                    lastUpdated = Date()
                    if !jobs.contains(where: \.active) { break }
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } while !Task.isCancelled
            } catch is CancellationError {
            } catch {
                guard !Task.isCancelled else { return }
                message = "Scan status unavailable: \(error.localizedDescription)"
            }
        }
    }
    private func scanCard(_ job: ScanJob) -> some View {
        HStack(alignment: .top, spacing: 10) {
            if job.active { ProgressView().controlSize(.small) }
            VStack(alignment: .leading, spacing: 3) {
                Text("\(job.status.capitalized) · \(job.scanned) scanned · \(job.errors) errors")
                    .font(.caption.weight(.medium))
                    .accessibilityIdentifier("scan-status-\(job.status)")
                Text(job.path).font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(2).textSelection(.enabled)
                if let error = job.error, !error.isEmpty {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
                if ["queued", "running"].contains(job.status) {
                    Button("Request cancellation") {
                        cancelingId = job.id; cancelMessage = ""
                        Task {
                            defer { cancelingId = nil; retry += 1 }
                            do { let state = try await controller.cancelNativeScan(job.id); cancelMessage = "Scan status: \(state). A job that finishes first may already have published its index." }
                            catch { cancelMessage = "Cancellation could not be confirmed: \(error.localizedDescription). Refresh status before retrying." }
                        }
                    }.disabled(cancelingId != nil).accessibilityIdentifier("scan-cancel-\(job.id)")
                }
            }
        }
        .accessibilityElement(children: .contain)
    }
}

struct EvidenceSummaryPanel: View {
    enum Mode { case library, health, capabilities }
    let controller: BackendController
    let mode: Mode
    @State private var summary: WorkspaceSummary?
    @State private var message = ""
    @State private var retry = 0
    @State private var requestID = UUID()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let summary {
                switch mode {
                case .library:
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), alignment: .top)], alignment: .leading, spacing: 14) {
                        NativeMetricCard(title: "Indexed tracks", value: count(summary, "files"), detail: "At the last scan", systemImage: "music.note.list")
                        NativeMetricCard(title: "Personal crates", value: "\(summary.crates)", detail: "App-owned crates", systemImage: "shippingbox")
                        NativeMetricCard(title: "Data boundary", value: "Local", detail: "No audio is copied", systemImage: "lock.shield")
                    }
                case .health:
                    Text("Indexed library health").font(.headline)
                    Text("Snapshot evidence from the last scan. No fresh file or external DJ-library validation is performed here.")
                        .font(.caption).foregroundStyle(.secondary)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), alignment: .top)], alignment: .leading, spacing: 14) {
                        NativeMetricCard(title: "Indexed files", value: count(summary, "files"), detail: "Across selected roots", systemImage: "music.note.list")
                        NativeMetricCard(title: "Missing at last scan", value: count(summary, "missing_at_last_scan"), detail: "May need a new scan", systemImage: "questionmark.folder")
                        NativeMetricCard(title: "Index errors", value: count(summary, "error_count"), detail: "Recorded indexing evidence", systemImage: "exclamationmark.triangle")
                        NativeMetricCard(title: "Duplicate hash groups", value: count(summary, "exact_hash_duplicate_groups"), detail: "No deletion is implied", systemImage: "doc.on.doc")
                    }
                case .capabilities:
                    Label(summary.commercialReady ? "Commercial readiness reported" : "Local alpha · release gates pending",
                          systemImage: summary.commercialReady ? "checkmark.shield" : "exclamationmark.shield")
                        .font(.headline)
                    Text("\(summary.pendingReleaseGates) release gates pending")
                        .accessibilityIdentifier("release-gate-count")
                        .font(.subheadline).foregroundStyle(.secondary)
                    ForEach(summary.gates) { gate in
                        capabilityRow(gate)
                    }
                    Divider()
                    Text("Export and provider destinations").font(.headline)
                    ForEach(summary.destinations) { destination in
                        capabilityRow(destination)
                    }
                }
                if !summary.healthAvailable && mode != .capabilities {
                    Text("Indexed health evidence is unavailable. Refresh after the catalog is initialized.")
                        .font(.caption).foregroundStyle(.orange)
                }
                Button("Refresh evidence") { retry += 1 }
                    .accessibilityIdentifier("refresh-evidence")
            } else if !message.isEmpty {
                NativeEmptyState(title: "Evidence unavailable", detail: message, systemImage: "exclamationmark.triangle")
                Button("Retry evidence") { retry += 1 }
            } else {
                ProgressView("Loading indexed evidence…")
            }
        }
        .task(id: retry) {
            let currentRequest = UUID()
            requestID = currentRequest
            message = ""
            controller.loadSummary { result in
                guard requestID == currentRequest else { return }
                switch result {
                case let .success(value): summary = value
                case let .failure(error): summary = nil; message = error.localizedDescription
                }
            }
        }
    }

    private func count(_ summary: WorkspaceSummary, _ key: String) -> String {
        guard summary.healthAvailable, let value = summary.healthCounts[key] else { return "—" }
        return String(value)
    }

    private func capabilityRow(_ item: CapabilityItem) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(item.name) · \(item.state.replacingOccurrences(of: "-", with: " "))")
                .font(.subheadline.weight(.semibold))
            Text(item.detail).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .combine)
    }
}

extension BackendController {
    private var workflowJournalURL: URL {
        URL(fileURLWithPath: dataPath, isDirectory: true).appendingPathComponent("native-workflow-v1.json")
    }

    private func loadWorkflowJournal() {
        do {
            let url = workflowJournalURL
            let journal: NativeWorkflowJournal
            if FileManager.default.fileExists(atPath: url.path) {
                let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
                guard attributes[.type] as? FileAttributeType == .typeRegular,
                      (attributes[.size] as? NSNumber)?.intValue ?? Int.max < 8 * 1024 * 1024 else {
                    throw nativeWorkflowError("Native recovery journal is not a supported regular file. Preserve it before repairing recovery storage.")
                }
                journal = try JSONDecoder().decode(NativeWorkflowJournal.self, from: Data(contentsOf: url))
                guard journal.schemaVersion == 1, journal.datasetId == datasetID else {
                    throw nativeWorkflowError("Native recovery belongs to another dataset or app version. Preserve the journal; no pending approval was sent.")
                }
                if let pending = journal.pending {
                    let body = try JSONSerialization.jsonObject(with: pending.requestData) as? [String: String]
                    guard pending.proposal.datasetId == datasetID,
                          body == ["request_id": pending.requestId, "dataset_id": datasetID,
                                   "proposal_id": pending.proposal.proposalId] else {
                        throw nativeWorkflowError("Pending approval identity is invalid. No approval was sent.")
                    }
                }
            } else { journal = NativeWorkflowJournal(datasetId: datasetID) }
            try journal.validatePendingOperations(dataset: datasetID)
            if let pending = journal.pendingEdit {
                let expected = try editRequest(pending.preview, undo: pending.undo, requestId: pending.requestId)
                guard let supplied = try JSONSerialization.jsonObject(with: pending.requestData) as? [String: Any],
                      let wanted = try JSONSerialization.jsonObject(with: expected) as? [String: Any],
                      NSDictionary(dictionary: supplied).isEqual(to: wanted) else {
                    throw nativeWorkflowError("Retained edit identity is invalid. No change was sent.")
                }
            }
            workflowJournal = journal
            workflowJournalError = ""
        } catch {
            workflowJournal = nil
            workflowJournalError = "Native approval recovery is unavailable: \(error.localizedDescription)"
        }
    }

    private func persistWorkflowJournal(_ value: NativeWorkflowJournal) throws {
        guard value.datasetId == datasetID, !dataPath.isEmpty else {
            throw nativeWorkflowError("Recovery storage is not ready for this dataset.")
        }
        let url = workflowJournalURL
        if FileManager.default.fileExists(atPath: url.path) {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular else {
                throw nativeWorkflowError("Recovery journal must be a regular file. Approval was not sent.")
            }
        }
        let data = try JSONEncoder().encode(value)
        guard data.count < 8 * 1024 * 1024 else { throw nativeWorkflowError("Native recovery journal exceeds its supported size.") }
        try data.write(to: url, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.synchronize()
        let directory = open(dataPath, O_RDONLY)
        guard directory >= 0 else { throw nativeWorkflowError("Could not synchronize approval recovery storage.") }
        defer { close(directory) }
        guard fsync(directory) == 0 else { throw nativeWorkflowError("Could not synchronize approval recovery storage.") }
        workflowJournal = value
    }

    func previewNativePlan(_ result: PlanResult, index: Int, name: String) async throws -> NativePlanProposal {
        guard result.response.alternatives.indices.contains(index), !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw nativeWorkflowError("Choose an alternative and enter a crate name.")
        }
        var body = try JSONSerialization.jsonObject(with: result.requestData) as! [String: Any]
        body["dataset_id"] = datasetID
        body["name"] = name
        body["alternative_index"] = index
        let raw = try await nativeBytes("api/studio/plan/propose", bodyData: JSONSerialization.data(withJSONObject: body))
        let proposal = try Self.decodeNative(NativePlanProposal.self, raw)
        let document = try JSONSerialization.jsonObject(with: raw) as! [String: Any]
        let original = try JSONSerialization.jsonObject(with: result.responseData) as! [String: Any]
        let expected = (original["alternatives"] as? [[String: Any]])?[index]
        let actual = (document["planning"] as? [String: Any])?["alternative"] as? [String: Any]
        guard proposal.datasetId == datasetID, proposal.name == name,
              proposal.libraryRevision == result.response.libraryRevision,
              proposal.tracks == result.response.alternatives[index].tracks,
              let expected, let actual,
              NSDictionary(dictionary: expected).isEqual(to: actual) else {
            throw nativeWorkflowError("The proposed set differs from the alternative you reviewed. Generate and review the current plan again.")
        }
        return proposal
    }

    func approveNativePlan(_ proposal: NativePlanProposal) async throws {
        guard !approvalBusy, var journal = workflowJournal, !hasPendingNativeOperation,
              proposal.datasetId == datasetID else {
            throw nativeWorkflowError("Resolve the existing approval recovery before saving another crate.")
        }
        let requestId = UUID().uuidString.lowercased()
        let data = try JSONSerialization.data(withJSONObject: ["request_id": requestId, "dataset_id": datasetID,
                                                              "proposal_id": proposal.proposalId], options: [.sortedKeys])
        journal.pending = NativeApprovalOperation(proposal: proposal, requestId: requestId, requestData: data)
        // Persist and synchronize exact intent BEFORE the mutation can reach the server.
        try persistWorkflowJournal(journal)
        try await retryNativeApproval()
    }

    func retryNativeApproval() async throws {
        guard !approvalBusy, let pending = workflowJournal?.pending, !pending.rejected else {
            throw nativeWorkflowError("There is no retryable approval.")
        }
        approvalBusy = true
        defer { approvalBusy = false }
        do {
            let raw = try await nativeBytes("api/studio/approve", bodyData: pending.requestData)
            let receipt = try Self.decodeNative(NativeApprovalReceipt.self, raw)
            guard receipt.ok, receipt.requestId == pending.requestId, receipt.datasetId == datasetID,
                  receipt.proposalId == pending.proposal.proposalId,
                  receipt.crateId == "studio-" + pending.proposal.proposalId,
                  receipt.revision == 1, receipt.entries == pending.proposal.tracks.count else {
                throw nativeWorkflowError("Approval response did not match the retained request. Retry to reconcile its exact receipt.")
            }
            // Fault injection is isolated to explicitly synthetic UI runs, after a real validated receipt.
            let arguments = ProcessInfo.processInfo.arguments
            if arguments.contains("--ui-testing"), arguments.contains("--synthetic-data"),
               arguments.contains("--test-drop-approval-ack"), !simulatedAcknowledgementLoss {
                simulatedAcknowledgementLoss = true
                throw nativeWorkflowError("Approval response interrupted. The exact request is retained for recovery.")
            }
            guard var journal = workflowJournal, journal.pending?.requestId == pending.requestId else {
                throw nativeWorkflowError("Approval recovery changed unexpectedly. Reopen and reconcile before continuing.")
            }
            let saved = NativeSavedCrate(id: receipt.crateId, revision: receipt.revision,
                                         name: pending.proposal.name, tracks: pending.proposal.tracks,
                                         requestId: pending.requestId)
            journal.saved.removeAll { $0.id == saved.id }
            journal.saved.insert(saved, at: 0)
            journal.saved = Array(journal.saved.prefix(50))
            journal.pending = nil
            try persistWorkflowJournal(journal)
        } catch {
            let failure = error as NSError
            if failure.domain == "BOOTHBUDDY", [400, 409].contains(failure.code), var journal = workflowJournal {
                // A definite server rejection is discardable; an unknown network outcome is not.
                journal.pending?.rejected = true
                try persistWorkflowJournal(journal)
            }
            throw error
        }
    }

    func discardRejectedNativeApproval() throws {
        guard !approvalBusy, var journal = workflowJournal, journal.pending?.rejected == true else {
            throw nativeWorkflowError("An uncertain approval must be reconciled, not discarded.")
        }
        journal.pending = nil
        try persistWorkflowJournal(journal)
    }

    func previewNativeExport(crateId: String, revision: Int) async throws -> NativeExportPreview {
        let body: [String: Any] = ["dataset_id": datasetID, "crate_id": crateId, "base_revision": revision]
        let raw = try await nativeBytes("api/catalog/personal/export/preview", bodyData: JSONSerialization.data(withJSONObject: body))
        let preview = try Self.decodeNative(NativeExportPreview.self, raw)
        guard preview.ok, preview.manifest.schemaVersion == 1, preview.manifest.datasetId == datasetID,
              preview.manifest.crate.id == crateId, preview.manifest.crate.revision == revision,
              preview.manifest.requested == preview.manifest.entries.count,
              preview.manifest.eligible + preview.manifest.blocked == preview.manifest.requested else {
            throw nativeWorkflowError("Export preview identity or counts did not reconcile.")
        }
        return preview
    }

    private func verifiedNativeExport(_ preview: NativeExportPreview) async throws -> (data: Data, manifest: Data) {
        guard preview.manifest.ready, preview.manifest.blocked == 0,
              preview.manifest.included == preview.manifest.requested else {
            throw nativeWorkflowError("Every crate entry must pass current export verification.")
        }
        let body: [String: Any] = ["dataset_id": datasetID, "crate_id": preview.manifest.crate.id,
                                  "base_revision": preview.manifest.crate.revision, "preview_id": preview.previewId]
        let raw = try await nativeBytes("api/catalog/personal/export/download", bodyData: JSONSerialization.data(withJSONObject: body))
        let response = try Self.decodeNative(NativeExportDownload.self, raw)
        guard response.ok, response.previewId == preview.previewId, response.manifest == preview.manifest,
              let data = Data(base64Encoded: response.bundleBase64), data.count <= 64 * 1024 * 1024,
              let document = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
              let manifest = document["manifest"] as? [String: Any] else {
            throw nativeWorkflowError("Export response did not match the reviewed preview.")
        }
        try NativePlaylistZIP.verify(data, expectedManifest: manifest, datasetId: datasetID)
        nativeExportRevision += 1
        return (data, try JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]))
    }

    func saveNativeExport(_ preview: NativeExportPreview) async throws -> URL? {
        guard let destination = await chooseNativeZIPDestination() else { return nil }
        let verified = try await verifiedNativeExport(preview)
        try writeNativeZIP(verified.data, to: destination)
        return destination
    }

    func saveNativeSeratoCrate(_ preview: NativeExportPreview) async throws -> URL? {
        guard let window = NSApp.keyWindow else { return nil }
        let panel = NSSavePanel()
        panel.title = "Export to Serato"
        panel.prompt = "Export"
        panel.allowedContentTypes = [UTType(filenameExtension: "crate") ?? .data]
        panel.nameFieldStringValue = NativeSeratoCrate.filename(for: preview.manifest.crate.name)
        panel.message = "Choose a new crate file. Quit Serato before saving into its Subcrates folder; existing crates will not be replaced."
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ui-testing"), arguments.contains("--synthetic-data") {
            panel.directoryURL = URL(fileURLWithPath: dataPath)
        } else {
            let standard = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Music/_Serato_/Subcrates", isDirectory: true)
            if FileManager.default.fileExists(atPath: standard.path) { panel.directoryURL = standard }
        }
        let selected: URL? = await withCheckedContinuation { continuation in
            panel.beginSheetModal(for: window) { response in continuation.resume(returning: response == .OK ? panel.url : nil) }
        }
        guard let selected else { return nil }
        let destination = try canonicalNativeFileURL(selected.deletingLastPathComponent()).appendingPathComponent(selected.lastPathComponent)
        guard destination.pathExtension.lowercased() == "crate", !FileManager.default.fileExists(atPath: destination.path) else {
            throw nativeWorkflowError("Choose a new .crate filename. Existing Serato crates will not be replaced.")
        }
        func checkSeratoClosed() throws {
            if NativeSeratoCrate.isSubcratesDestination(destination),
               !NSRunningApplication.runningApplications(withBundleIdentifier: "com.serato.seratodj").isEmpty {
                throw nativeWorkflowError("Quit Serato before exporting into its Subcrates folder, then reopen it to load the new crate. You can also save the .crate file to another folder.")
            }
        }
        try checkSeratoClosed()
        _ = try await verifiedNativeExport(preview)
        let crate = try NativeSeratoCrate.data(preview.manifest)
        try checkSeratoClosed()
        try writeNativeZIP(crate, to: destination, replaceExisting: false)
        return destination
    }

    func nativeExportHistory() async throws -> NativeExportHistory {
        let raw = try await nativeBytes("api/catalog/personal/exports")
        return try Self.decodeNative(NativeExportHistory.self, raw)
    }

    func saveHistoricalNativeExport(_ item: NativeExportHistoryItem) async throws -> URL? {
        guard item.state == "historical", let crate = item.crate else {
            throw nativeWorkflowError("This saved export is unavailable.")
        }
        guard let destination = await chooseNativeZIPDestination() else { return nil }
        let data = try await nativeBytes("api/catalog/personal/exports/download", query: [
            URLQueryItem(name: "dataset_id", value: datasetID), URLQueryItem(name: "export_id", value: item.exportId)
        ])
        let manifest = try NativePlaylistZIP.verify(data, expectedManifest: nil, datasetId: datasetID)
        guard manifest.crate == crate, manifest.included == item.entries else {
            throw nativeWorkflowError("Historical export identity did not match the selected artifact.")
        }
        try writeNativeZIP(data, to: destination)
        return destination
    }

    private func writeNativeZIP(_ data: Data, to destination: URL, replaceExisting: Bool = true) throws {
        // Set private permissions before writing any database or support-report bytes.
        let temporary = destination.deletingLastPathComponent().appendingPathComponent(".booth-buddy-\(UUID().uuidString).tmp")
        let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard descriptor >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
        defer { try? handle.close(); unlink(temporary.path) }
        try handle.write(contentsOf: data)
        try handle.synchronize()
        let published = replaceExisting ? rename(temporary.path, destination.path) : link(temporary.path, destination.path)
        guard published == 0 else {
            if !replaceExisting && errno == EEXIST { throw nativeWorkflowError("The destination already exists. Choose a new crate filename.") }
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        }
    }

    private func chooseNativeZIPDestination(filename: String = "BOOTH BUDDY playlist.zip", title: String = "Save verified playlist bundle") async -> URL? {
        guard let window = NSApp.keyWindow else { return nil }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.zip]
        panel.nameFieldStringValue = filename
        panel.title = title
        if ProcessInfo.processInfo.arguments.contains("--ui-testing"),
           ProcessInfo.processInfo.arguments.contains("--synthetic-data") {
            panel.directoryURL = URL(fileURLWithPath: dataPath, isDirectory: true)
        }
        return await withCheckedContinuation { continuation in
            panel.beginSheetModal(for: window) { response in
                continuation.resume(returning: response == .OK ? panel.url : nil)
            }
        }
    }
}

// Reads the backend's bounded, stored ZIP format. Never extracts archive paths.
enum NativePlaylistZIP {
    @discardableResult
    static func verify(_ data: Data, expectedManifest: [String: Any]?, datasetId: String) throws -> NativeExportManifest {
        guard data.count >= 22, data.count <= 64 * 1024 * 1024 else { throw invalid() }
        let bytes = [UInt8](data)
        func value(_ offset: Int, _ length: Int) throws -> Int {
            guard offset >= 0, length <= 4, offset + length <= bytes.count else { throw invalid() }
            return (0..<length).reduce(0) { $0 | Int(bytes[offset + $1]) << ($1 * 8) }
        }
        let end = bytes.count - 22
        guard (try value(end, 4)) == 0x06054b50, (try value(end + 4, 2)) == 0, (try value(end + 6, 2)) == 0,
              (try value(end + 8, 2)) == 3, (try value(end + 10, 2)) == 3, (try value(end + 20, 2)) == 0 else { throw invalid() }
        let centralStart = (try value(end + 16, 4))
        guard centralStart + (try value(end + 12, 4)) == end else { throw invalid() }
        var members: [String: Data] = [:]
        var identities: [String: (offset: Int, crc: Int, size: Int)] = [:]
        var offset = 0
        while offset < centralStart {
            let start = offset
            guard (try value(start, 4)) == 0x04034b50, (try value(start + 6, 2)) == 0,
                  (try value(start + 8, 2)) == 0 else { throw invalid() }
            let size = (try value(start + 18, 4))
            guard (try value(start + 22, 4)) == size else { throw invalid() }
            let nameLength = (try value(start + 26, 2))
            let extraLength = (try value(start + 28, 2))
            let nameStart = start + 30
            let payloadStart = nameStart + nameLength + extraLength
            guard payloadStart <= centralStart, size <= centralStart - payloadStart,
                  let name = String(bytes: bytes[nameStart..<(nameStart + nameLength)], encoding: .utf8),
                  ["manifest.json", "playlist.csv", "playlist.m3u8"].contains(name), members[name] == nil else { throw invalid() }
            let payload = Data(bytes[payloadStart..<(payloadStart + size)])
            let checksum = (try value(start + 14, 4))
            guard Int(crc32(payload)) == checksum else { throw invalid() }
            members[name] = payload
            identities[name] = (start, checksum, size)
            offset = payloadStart + size
        }
        guard offset == centralStart, members.count == 3 else { throw invalid() }
        var centralNames = Set<String>()
        for _ in 0..<3 {
            guard (try value(offset, 4)) == 0x02014b50, (try value(offset + 8, 2)) == 0,
                  (try value(offset + 10, 2)) == 0, (try value(offset + 34, 2)) == 0 else { throw invalid() }
            let nameLength = (try value(offset + 28, 2))
            let extraLength = (try value(offset + 30, 2))
            let commentLength = (try value(offset + 32, 2))
            let next = offset + 46 + nameLength + extraLength + commentLength
            guard next <= end,
                  let name = String(bytes: bytes[(offset + 46)..<(offset + 46 + nameLength)], encoding: .utf8),
                  let identity = identities[name], centralNames.insert(name).inserted,
                  (try value(offset + 16, 4)) == identity.crc, (try value(offset + 20, 4)) == identity.size,
                  (try value(offset + 24, 4)) == identity.size, (try value(offset + 42, 4)) == identity.offset else { throw invalid() }
            offset = next
        }
        guard offset == end, let manifestData = members["manifest.json"],
              let document = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any] else { throw invalid() }
        if let expectedManifest, !NSDictionary(dictionary: document).isEqual(to: expectedManifest) { throw invalid() }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(NativeExportManifest.self, from: manifestData)
        let paths = manifest.entries.compactMap(\.path)
        guard manifest.schemaVersion == 1, manifest.datasetId == datasetId, manifest.ready,
              manifest.blocked == 0, manifest.blockers.isEmpty, !paths.isEmpty,
              manifest.included == paths.count, manifest.requested == paths.count,
              manifest.eligible == paths.count, manifest.entries.allSatisfy({ $0.eligible && $0.reason.isEmpty }),
              members["playlist.m3u8"] == Data(("#EXTM3U\n" + paths.map { $0 + "\n" }.joined()).utf8),
              let csvData = members["playlist.csv"] else { throw invalid() }
        let rows = try csvRows(csvData)
        guard let header = rows.first, let pathColumn = header.firstIndex(of: "path"),
              rows.count == paths.count + 1,
              rows.dropFirst().allSatisfy({ $0.count == header.count }),
              rows.dropFirst().map({ $0[pathColumn] }) == paths else { throw invalid() }
        return manifest
    }

    private static let crcTable: [UInt32] = (0..<256).map { index in
        var value = UInt32(index)
        for _ in 0..<8 { value = value & 1 == 0 ? value >> 1 : (value >> 1) ^ 0xedb88320 }
        return value
    }

    private static func crc32(_ data: Data) -> UInt32 {
        var value: UInt32 = 0xffffffff
        for byte in data { value = (value >> 8) ^ crcTable[Int((value ^ UInt32(byte)) & 255)] }
        return value ^ 0xffffffff
    }

    private static func csvRows(_ data: Data) throws -> [[String]] {
        let bytes = [UInt8](data)
        var rows: [[String]] = [], row: [String] = [], field: [UInt8] = []
        var quoted = false, closed = false, index = 0
        func string(_ bytes: [UInt8]) throws -> String {
            guard let value = String(bytes: bytes, encoding: .utf8) else { throw invalid() }
            return value
        }
        while index < bytes.count {
            let byte = bytes[index]
            if quoted {
                if byte == 34 {
                    if index + 1 < bytes.count && bytes[index + 1] == 34 { field.append(34); index += 1 }
                    else { quoted = false; closed = true }
                } else { field.append(byte) }
            } else if byte == 34 && field.isEmpty && !closed { quoted = true }
            else if byte == 44 {
                row.append(try string(field)); field = []; closed = false
            } else if byte == 10 || byte == 13 {
                if byte == 13 {
                    guard index + 1 < bytes.count && bytes[index + 1] == 10 else { throw invalid() }
                    index += 1
                }
                row.append(try string(field)); rows.append(row)
                row = []; field = []; closed = false
            } else {
                guard !closed else { throw invalid() }
                field.append(byte)
            }
            index += 1
        }
        guard !quoted else { throw invalid() }
        if !field.isEmpty || !row.isEmpty || closed { row.append(try string(field)); rows.append(row) }
        return rows
    }

    private static func invalid() -> NSError {
        nativeWorkflowError("The playlist ZIP failed native integrity, membership or order verification. No destination file was saved.")
    }
}

struct NativePlanApprovalView: View {
    @ObservedObject var controller: BackendController
    let proposal: NativePlanProposal
    @State private var message = ""

    private var alreadySaved: Bool {
        controller.workflowJournal?.saved.contains(where: { $0.id == "studio-" + proposal.proposalId }) == true
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Review exact proposal: \(proposal.name)").font(.headline)
            Text("\(proposal.tracks.count) ordered files · expires \(Date(timeIntervalSince1970: proposal.expiresAt), style: .time)")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(Array(proposal.tracks.enumerated()), id: \.offset) { index, track in
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(index + 1). \(track.title) — \(track.artist)").font(.subheadline)
                    Text(track.path).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                    Text("SHA-256 \(track.contentHash)").font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }
            Text("Approval creates this static app-owned crate. Source audio, tags and external DJ libraries are unchanged. The server rechecks the proposal and exact files.")
                .font(.caption).foregroundStyle(.secondary)
            Button(alreadySaved ? "Crate saved" : "Approve and save crate") {
                message = ""
                Task {
                    do { try await controller.approveNativePlan(proposal); message = "Crate saved with a verified approval receipt." }
                    catch { message = error.localizedDescription }
                }
            }
            .buttonStyle(.borderedProminent).tint(BoothBrand.buttonFill).foregroundStyle(.white).accessibilityIdentifier("plan-approve")
            .disabled(alreadySaved || controller.approvalBusy || controller.workflowJournal == nil || controller.hasPendingNativeOperation)
            if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled) }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct NativeApprovalRecoveryView: View {
    @ObservedObject var controller: BackendController
    @State private var errorMessage = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !controller.workflowJournalError.isEmpty {
                Text(controller.workflowJournalError).foregroundStyle(.red).font(.caption).accessibilityIdentifier("native-recovery-error")
            }
            if let pending = controller.workflowJournal?.pending {
                Text(pending.rejected ? "Approval rejected" : "Approval needs reconciliation")
                    .font(.headline).accessibilityIdentifier("native-pending-approval")
                Text("\(pending.proposal.name) · \(pending.proposal.tracks.count) exact files · request \(pending.requestId)")
                    .font(.caption).textSelection(.enabled)
                Text(pending.rejected
                     ? "The server explicitly rejected this request. Discard it before preparing a fresh proposal."
                     : "The original approval request is retained on this Mac. Retry it to retrieve the same receipt; do not create a replacement approval.")
                    .font(.caption).foregroundStyle(.secondary)
                if pending.rejected {
                    Button("Discard rejected approval") {
                        do { try controller.discardRejectedNativeApproval(); errorMessage = "" }
                        catch { errorMessage = error.localizedDescription }
                    }
                    .disabled(controller.approvalBusy)
                } else {
                    Button(controller.approvalBusy ? "Reconciling…" : "Retry exact approval") {
                        errorMessage = ""
                        Task {
                            do { try await controller.retryNativeApproval() }
                            catch { errorMessage = error.localizedDescription }
                        }
                    }
                    .accessibilityIdentifier("native-retry-approval").disabled(controller.approvalBusy)
                }
            }
            if !errorMessage.isEmpty { Text(errorMessage).font(.caption).foregroundStyle(.red) }
        }
    }
}

struct NativeSavedPlansView: View {
    @ObservedObject var controller: BackendController

    var body: some View {
        if let saved = controller.workflowJournal?.saved, !saved.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                Text("Saved planned crates").font(.headline)
                Text("Recent native approval receipts on this Mac. Export previews check the current crate revision and source bytes again.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(saved) { crate in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(crate.name).font(.subheadline.weight(.semibold)).accessibilityIdentifier("native-saved-crate-name")
                        Text("\(crate.tracks.count) files · revision \(crate.revision) · receipt \(crate.requestId.prefix(8))")
                            .font(.caption).foregroundStyle(.secondary)
                        NativeCrateExportView(controller: controller, crateId: crate.id, revision: crate.revision, name: crate.name)
                    }
                    .padding(12).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }
}

struct NativeCrateExportView: View {
    @ObservedObject var controller: BackendController
    let crateId: String
    let revision: Int
    let name: String
    @State private var preview: NativeExportPreview?
    @State private var busy = false
    @State private var message = ""
    @State private var errorMessage = ""
    @State private var eventReviewed = false
    @State private var playbackReviewed = false
    @State private var checkedAt: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Pre-gig checks").font(.headline)
            Text("For \(name), revision \(revision). These checks apply to this crate; they do not certify an entire event or playback.")
                .font(.caption).foregroundStyle(.secondary)
            if controller.hasPendingNativeOperation {
                Label("A saved change still needs reconciliation. Review recovery before relying on this crate.", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange).accessibilityIdentifier("gig-recovery-warning")
            }
            Text(preview == nil ? "File availability: not checked for this view. Verify the current crate below." : (preview!.manifest.ready ? "File availability: verified at the last preview. Saving checks source bytes again." : "File availability: blocked. Resolve the listed memberships before exporting."))
                .font(.caption).accessibilityIdentifier("gig-file-status")
            if let checkedAt { Text("Last preview: \(checkedAt.formatted(date: .abbreviated, time: .shortened))").font(.caption2).foregroundStyle(.secondary) }
            Toggle("I reviewed event must-plays, do-not-plays and unresolved requests against this crate", isOn: $eventReviewed)
                .font(.caption).accessibilityIdentifier("gig-event-review")
            Toggle("I checked drive availability, track order and playback in my DJ software", isOn: $playbackReviewed)
                .font(.caption).accessibilityIdentifier("gig-playback-review")
            Text("DJ review: \(eventReviewed && playbackReviewed ? "both items acknowledged for this view" : "still needed"). These acknowledgements are not an automated check and reset when you verify again or reopen the crate.")
                .font(.caption2).foregroundStyle(.secondary).accessibilityIdentifier("gig-review-status")
            Button(busy ? "Verifying…" : "Preview verified export") {
                preview = nil; checkedAt = nil; eventReviewed = false; playbackReviewed = false
                busy = true; message = ""; errorMessage = ""
                Task {
                    defer { busy = false }
                    do { preview = try await controller.previewNativeExport(crateId: crateId, revision: revision); checkedAt = Date() }
                    catch { errorMessage = error.localizedDescription }
                }
            }
            .accessibilityIdentifier("native-export-preview").disabled(busy)
            if let preview {
                Text("\(preview.manifest.requested) requested · \(preview.manifest.eligible) verified · \(preview.manifest.blocked) blocked")
                    .font(.caption.weight(.medium)).accessibilityIdentifier("native-export-counts")
                ForEach(preview.manifest.blockers, id: \.self) { Text($0).font(.caption).foregroundStyle(.red) }
                ForEach(Array(preview.manifest.entries.enumerated()), id: \.offset) { _, entry in
                    if !entry.eligible {
                        Text("Entry \(entry.position): \(entry.reason)").font(.caption).foregroundStyle(.red)
                    }
                }
                Text("ZIP contains ordered M3U8, CSV and a private verification manifest; no audio. Extract before importing into DJ software. This does not certify playback, cue writes or musical quality.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Save verified ZIP…") {
                    busy = true; message = ""; errorMessage = ""
                    Task {
                        defer { busy = false }
                        do {
                            if let url = try await controller.saveNativeExport(preview) {
                                message = "Saved verified ZIP: \(url.path)"
                            } else { message = "Save canceled. No destination file was written." }
                        } catch { self.preview = nil; errorMessage = error.localizedDescription }
                    }
                }
                .accessibilityIdentifier("native-export-save").disabled(busy || !preview.manifest.ready)
                Button("Export to Serato…") {
                    busy = true; message = ""; errorMessage = ""
                    Task {
                        defer { busy = false }
                        do {
                            if let destination = try await controller.saveNativeSeratoCrate(preview) {
                                message = NativeSeratoCrate.completionMessage(for: destination)
                            } else { message = "Serato export canceled." }
                        } catch { errorMessage = error.localizedDescription }
                    }
                }
                .accessibilityIdentifier("native-export-serato").disabled(busy || !preview.manifest.ready)
                Text("Exports a new Serato .crate with these exact memberships. Audio, cues, tags, existing crates and Serato’s database are unchanged.")
                    .font(.caption).foregroundStyle(.secondary)

            }

            if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled).accessibilityIdentifier("native-export-message") }
            if !errorMessage.isEmpty { Text(errorMessage).font(.caption).foregroundStyle(.red).textSelection(.enabled).accessibilityIdentifier("native-export-error") }
        }
        .onChange(of: crateId) { _ in resetChecks() }
        .onChange(of: revision) { _ in resetChecks() }
    }
    private func resetChecks() {
        preview = nil; checkedAt = nil; eventReviewed = false; playbackReviewed = false; message = ""; errorMessage = ""
    }
}

struct NativeExportHistoryView: View {
    @ObservedObject var controller: BackendController
    @State private var history: NativeExportHistory?
    @State private var retry = 0
    @State private var busy = false
    @State private var message = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Saved export history").font(.headline)
            Text("Historical bundles are integrity-checked snapshots, not proof that their source files remain available now.")
                .font(.caption).foregroundStyle(.secondary)
            if let history {
                Text("\(history.total) saved exports · showing up to 20 recent artifacts").font(.caption)
                ForEach(history.exports) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.crate?.name ?? "Unavailable export").font(.subheadline.weight(.medium))
                        Text(item.error ?? "Historical · \(item.entries ?? 0) entries").font(.caption).foregroundStyle(.secondary)
                        Button("Save historical ZIP…") {
                            busy = true; message = ""
                            Task {
                                defer { busy = false }
                                do {
                                    if let url = try await controller.saveHistoricalNativeExport(item) {
                                        message = "Saved historical ZIP: \(url.path)"
                                    } else { message = "Save canceled." }
                                } catch { message = error.localizedDescription }
                            }
                        }
                        .accessibilityIdentifier("native-history-save").disabled(busy || item.state != "historical")
                    }
                }
            }
            Button("Refresh export history") { retry += 1 }.disabled(busy)
            if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled) }
        }
        .task(id: "\(retry)-\(controller.nativeExportRevision)") {
            do { history = try await controller.nativeExportHistory(); message = "" }
            catch { guard !Task.isCancelled else { return }; message = error.localizedDescription }
        }
    }
}

extension BackendController {
    var hasPendingNativeOperation: Bool { workflowJournal?.pending != nil || workflowJournal?.pendingEdit != nil || workflowJournal?.pendingInboxReview != nil }

    func nativeCrates() async throws -> [CrateSummary] {
        let state = try await nativeRequest("api/studio/state")
        guard let rows = state["crates"] as? [[String: Any]] else { throw nativeWorkflowError("Crate list is unavailable.") }
        return try rows.map { row in
            guard let id = row["id"] as? String, let name = row["name"] as? String,
                  let revision = row["revision"] as? Int, let count = row["entries"] as? Int else {
                throw nativeWorkflowError("Crate list contains incomplete revision evidence.")
            }
            return CrateSummary(id: id, name: name, entries: count, revision: revision)
        }
    }

    func nativeCrateDetail(_ id: String) async throws -> NativeCrateDetail {
        let raw = try await nativeBytes("api/catalog/personal/detail", query: [URLQueryItem(name: "crate_id", value: id)])
        let detail = try Self.decodeNative(NativeCrateDetail.self, raw)
        guard detail.id == id else { throw nativeWorkflowError("Crate detail identity changed unexpectedly.") }
        return detail
    }

    func nativeEditHistory() async throws -> [NativeEditHistoryItem] {
        let state = try await nativeRequest("api/studio/state")
        let raw = try JSONSerialization.data(withJSONObject: state["edits"] as? [[String: Any]] ?? [])
        return try Self.decodeNative([NativeEditHistoryItem].self, raw)
    }

    func previewNativeCrateEdit(_ detail: NativeCrateDetail, name: String, order: [String]) async throws -> NativeEditPreview {
        let original = detail.entries.map(\.id)
        guard Set(order).count == order.count, Set(order).isSubset(of: Set(original)),
              !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw nativeWorkflowError("The draft needs a name and distinct memberships from this crate.")
        }
        var operations: [[String: Any]] = []
        let base: [String: Any] = ["crate_id": detail.id, "base_revision": detail.revision]
        if name != detail.name { operations.append(base.merging(["action": "rename", "name": name]) { _, new in new }) }
        let removed = original.filter { !order.contains($0) }
        if !removed.isEmpty { operations.append(base.merging(["action": "remove", "entry_ids": removed]) { _, new in new }) }
        if original.filter({ order.contains($0) }) != order {
            operations.append(base.merging(["action": "reorder", "entry_ids": order]) { _, new in new })
        }
        guard !operations.isEmpty else { throw nativeWorkflowError("Change the name or membership order before previewing.") }
        let raw = try await nativeBytes("api/studio/edit/preview", bodyData: JSONSerialization.data(withJSONObject: ["dataset_id": datasetID, "operations": operations]))
        let preview = try Self.decodeNative(NativeEditPreview.self, raw)
        let entries = Dictionary(uniqueKeysWithValues: detail.entries.map { ($0.id, $0.membership) })
        let desired = NativeCrateSnapshot(id: detail.id, name: name, revision: detail.revision, entries: order.map { entries[$0]! })
        guard preview.datasetId == datasetID, preview.before == [detail.id: detail.snapshot], preview.after == [detail.id: desired] else {
            throw nativeWorkflowError("The edit proposal differs from the exact draft. Refresh and review again.")
        }
        return preview
    }

    private func editRequest(_ preview: NativeEditPreview, undo: Bool, requestId: String) throws -> Data {
        guard preview.datasetId == datasetID, !preview.before.isEmpty,
              Set(preview.before.keys) == Set(preview.after.keys) else {
            throw nativeWorkflowError("Edit evidence belongs to another dataset or has inconsistent crate identities.")
        }
        return try JSONSerialization.data(withJSONObject: ["request_id": requestId, "dataset_id": datasetID,
            "edit_id": preview.editId, "expected_crates": preview.expectedCrates(undo: undo).map(\.body)], options: [.sortedKeys])
    }

    func approveNativeEdit(_ preview: NativeEditPreview, undo: Bool = false) async throws {
        guard !approvalBusy, !hasPendingNativeOperation, var journal = workflowJournal else {
            throw nativeWorkflowError("Resolve pending recovery before approving another change.")
        }
        let requestId = UUID().uuidString.lowercased()
        let data = try editRequest(preview, undo: undo, requestId: requestId)
        journal.pendingEdit = NativePendingEdit(preview: preview, undo: undo, requestId: requestId, requestData: data)
        try persistWorkflowJournal(journal)
        try await retryNativeEdit()
    }

    func retryNativeEdit() async throws {
        guard !approvalBusy, let pending = workflowJournal?.pendingEdit, !pending.rejected else {
            throw nativeWorkflowError("There is no retryable crate edit.")
        }
        approvalBusy = true
        defer { approvalBusy = false }
        do {
            let endpoint = pending.undo ? "api/studio/edit/undo" : "api/studio/edit/approve"
            let raw = try await nativeBytes(endpoint, bodyData: pending.requestData)
            let receipt = try Self.decodeNative(NativeEditReceipt.self, raw)
            guard receipt.ok, receipt.datasetId == datasetID, receipt.requestId == pending.requestId,
                  receipt.editId == pending.preview.editId, receipt.operation == (pending.undo ? "undo" : "approve"),
                  receipt.crates == pending.preview.expectedCrates(undo: pending.undo) else {
                throw nativeWorkflowError("Crate edit receipt did not match the retained request. Retry exact recovery.")
            }
            let args = ProcessInfo.processInfo.arguments
            if args.contains("--ui-testing"), args.contains("--synthetic-data"),
               args.contains("--test-drop-edit-ack"), !simulatedEditAcknowledgementLoss {
                simulatedEditAcknowledgementLoss = true
                throw nativeWorkflowError("Edit response interrupted. The exact change is retained for recovery.")
            }
            guard var journal = workflowJournal, journal.pendingEdit?.requestId == pending.requestId else {
                throw nativeWorkflowError("Edit recovery changed unexpectedly. Reopen before continuing.")
            }
            journal.pendingEdit = nil
            try persistWorkflowJournal(journal)
            nativeWorkspaceRevision += 1
        } catch {
            let errorValue = error as NSError
            if errorValue.domain == "BOOTHBUDDY", [400, 409].contains(errorValue.code), var journal = workflowJournal {
                journal.pendingEdit?.rejected = true
                try persistWorkflowJournal(journal)
            }
            throw error
        }
    }

    func discardRejectedNativeEdit() throws {
        guard !approvalBusy, var journal = workflowJournal, journal.pendingEdit?.rejected == true else {
            throw nativeWorkflowError("An uncertain edit must be reconciled, not discarded.")
        }
        journal.pendingEdit = nil
        try persistWorkflowJournal(journal)
    }
}

struct NativeEditRecoveryView: View {
    @ObservedObject var controller: BackendController
    @State private var message = ""
    var body: some View {
        if let pending = controller.workflowJournal?.pendingEdit {
            VStack(alignment: .leading, spacing: 8) {
                Text(pending.rejected ? "Crate change rejected" : "Crate change needs reconciliation")
                    .font(.headline).accessibilityIdentifier("native-pending-edit")
                Text("The exact \(pending.undo ? "undo" : "approval") request is retained on this Mac. Request \(pending.requestId)")
                    .font(.caption).textSelection(.enabled)
                Button(pending.rejected ? "Discard rejected change" : "Retry exact crate change") {
                    message = ""
                    Task {
                        do {
                            if pending.rejected { try controller.discardRejectedNativeEdit() }
                            else { try await controller.retryNativeEdit() }
                        } catch { message = error.localizedDescription }
                    }
                }
                .accessibilityIdentifier("native-retry-edit").disabled(controller.approvalBusy)
                if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red) }
            }
        }
    }
}

struct NativeCrateManager: View {
    @ObservedObject var controller: BackendController
    @State private var crates: [CrateSummary] = []
    @State private var selectedId = ""
    @State private var detail: NativeCrateDetail?
    @State private var name = ""
    @State private var order: [String] = []
    @State private var history: [NativeEditHistoryItem] = []
    @State private var review: NativeEditPreview?
    @State private var undo = false
    @State private var loading = false
    @State private var message = ""
    @State private var readId = UUID()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Edit personal crates").font(.headline)
            Text("Name, order and membership changes stay in a draft until you review and approve them. Source audio and external DJ libraries are unchanged.")
                .font(.caption).foregroundStyle(.secondary)
            Picker("Personal crate", selection: Binding(get: { selectedId }, set: { value in
                selectedId = value
                Task { await loadDetail() }
            })) {
                if crates.isEmpty { Text("No saved crates").tag("") }
                ForEach(crates) { crate in Text(crate.name).tag(crate.id) }
            }
            .accessibilityIdentifier("native-crate-picker")
            Button("Refresh current crates") { Task { await loadAll() } }.disabled(loading || controller.approvalBusy)
            if loading { ProgressView("Loading current revision…") }
            if let detail {
                Text(detail.name).font(.title3.weight(.semibold)).accessibilityIdentifier("native-current-crate-name")
                Text("Current revision \(detail.revision) · \(detail.entries.count) memberships")
                    .font(.caption).accessibilityIdentifier("native-current-crate-revision")
                TextField("Crate name", text: $name).textFieldStyle(.roundedBorder).accessibilityIdentifier("native-edit-name")
                ForEach(Array(order.enumerated()), id: \.element) { position, id in
                    if let track = detail.entries.first(where: { $0.id == id }) {
                        HStack(alignment: .top, spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(position + 1). \(track.song.title ?? "Untitled") — \(track.song.artist ?? "Unknown artist")")
                                    .font(.subheadline.weight(.semibold))
                                Text(track.selectedFile?.path ?? "No matched file; export will require current verification")
                                    .font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                            Spacer()
                            Button { order.swapAt(position, position - 1) } label: { Image(systemName: "arrow.up") }
                                .disabled(position == 0).accessibilityLabel("Move membership \(position + 1) up").accessibilityIdentifier("crate-up-\(id)")
                            Button { order.swapAt(position, position + 1) } label: { Image(systemName: "arrow.down") }
                                .disabled(position + 1 == order.count).accessibilityLabel("Move membership \(position + 1) down").accessibilityIdentifier("crate-down-\(id)")
                            Button("Remove") { order.removeAll { $0 == id } }.accessibilityIdentifier("crate-remove-\(id)")
                        }
                    }
                }
                Button("Reset edit draft") { name = detail.name; order = detail.entries.map(\.id); review = nil }
                Button("Preview crate changes") {
                    let draftName = name, draftOrder = order
                    review = nil; message = ""; loading = true
                    Task {
                        defer { loading = false }
                        do {
                            let preview = try await controller.previewNativeCrateEdit(detail, name: draftName, order: draftOrder)
                            guard selectedId == detail.id, name == draftName, order == draftOrder else { return }
                            undo = false; review = preview
                        } catch { message = error.localizedDescription }
                    }
                }
                .accessibilityIdentifier("native-edit-preview")
                .disabled(loading || controller.approvalBusy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                          (name == detail.name && order == detail.entries.map(\.id)))
                if let review { reviewCard(review) }
                NativeCrateExportView(controller: controller, crateId: detail.id, revision: detail.revision, name: detail.name)
                    .id("\(detail.id)-\(detail.revision)")
                Divider()
                Text("Recover reviewed changes").font(.headline)
                ForEach(history.filter { $0.document.before[selectedId] != nil }) { item in
                    HStack {
                        Text(item.undo != nil ? "Undone change" : (item.approval != nil ? "Approved change" : "Unapproved preview"))
                            .font(.caption)
                        Spacer()
                        if item.approval != nil && item.undo == nil {
                            Button("Review undo") { undo = true; review = item.preview; message = "" }
                                .accessibilityIdentifier("native-edit-review-undo")
                        }
                    }
                }
            } else if !loading && crates.isEmpty {
                Text("Approve a planned set to create your first personal crate.").font(.subheadline).foregroundStyle(.secondary)
            }
            if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.red).textSelection(.enabled).accessibilityIdentifier("native-edit-error") }
        }
        .onChange(of: name) { _ in review = nil }
        .onChange(of: order) { _ in review = nil }
        .task(id: controller.nativeWorkspaceRevision) { await loadAll() }
    }

    private func reviewCard(_ preview: NativeEditPreview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(undo ? "Review restoration" : "Review exact changes").font(.headline)
            ForEach(preview.before.keys.sorted(), id: \.self) { id in
                if let before = preview.before[id], let after = preview.after[id] {
                    let source = undo ? after : before
                    let target = undo ? before : after
                    Text("\(source.name) → \(target.name) · \(source.entries.count) → \(target.entries.count) memberships")
                        .font(.subheadline)
                    Text("Resulting membership order: \(target.entries.map(\.id).joined(separator: ", "))")
                        .font(.caption).textSelection(.enabled)
                }
            }
            Text("Approval applies all listed crate changes atomically. Undo refuses if any affected crate has newer changes.")
                .font(.caption).foregroundStyle(.secondary)
            Button(undo ? "Confirm undo" : "Approve crate changes") {
                message = ""
                Task {
                    do { try await controller.approveNativeEdit(preview, undo: undo); review = nil }
                    catch { message = error.localizedDescription }
                }
            }
            .buttonStyle(.borderedProminent).tint(BoothBrand.buttonFill).foregroundStyle(.white).accessibilityIdentifier(undo ? "native-edit-confirm-undo" : "native-edit-approve")
            .disabled(controller.approvalBusy || controller.hasPendingNativeOperation || controller.workflowJournal == nil)
            Button("Cancel review") { review = nil }
        }
        .padding(12).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private func loadAll() async {
        loading = true; message = ""
        do {
            let values = try await controller.nativeCrates()
            try Task.checkCancellation()
            crates = values
            if !values.contains(where: { $0.id == selectedId }) { selectedId = values.first?.id ?? "" }
            await loadDetail()
        } catch { guard !Task.isCancelled else { return }; message = error.localizedDescription; loading = false }
    }

    private func loadDetail() async {
        let currentRead = UUID(); readId = currentRead
        let id = selectedId
        detail = nil; review = nil; loading = true
        defer { if readId == currentRead { loading = false } }
        guard !id.isEmpty else { history = []; return }
        do {
            let value = try await controller.nativeCrateDetail(id)
            let changes = try await controller.nativeEditHistory()
            guard readId == currentRead, selectedId == id, !Task.isCancelled else { return }
            detail = value; name = value.name; order = value.entries.map(\.id); history = changes
        } catch { guard readId == currentRead, !Task.isCancelled else { return }; message = error.localizedDescription }
    }
}

struct NativeBackupManifest: Codable, Equatable, Sendable {
    let format: String
    let schemaVersion: Int
    let databaseBytes: Int
    let databaseSha256: String
    let schemaDigest: String
    let tableCounts: [String: Int]
    let note: String
}

// Invoke only the bundled, fixed backend command with structured arguments, never a shell.
enum NativeBackupTool {
    static func restore(executable: URL, archive: URL, destination: URL) throws -> NativeBackupManifest {
        guard !FileManager.default.fileExists(atPath: destination.path),
              destination.standardizedFileURL == (try canonicalNativeFileURL(destination)) else {
            throw nativeWorkflowError("Restore requires a new canonical folder. Existing data will not be replaced.")
        }
        let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("BOOTH-BUDDY-restore-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: temporary) }
        let outputURL = temporary.appendingPathComponent("result.json")
        let errorURL = temporary.appendingPathComponent("error.txt")
        FileManager.default.createFile(atPath: outputURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
        FileManager.default.createFile(atPath: errorURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let output = try FileHandle(forWritingTo: outputURL), errors = try FileHandle(forWritingTo: errorURL)
        defer { try? output.close(); try? errors.close() }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["--data", destination.path, "--restore", archive.path]
        var environment = ProcessInfo.processInfo.environment
        for key in environment.keys where ["PYTHON", "AUTOCUE_", "DYLD_", "XCTest", "XCInject", "__XCODE", "LLVM_PROFILE_"].contains(where: key.hasPrefix) { environment.removeValue(forKey: key) }
        process.environment = environment
        process.currentDirectoryURL = temporary
        process.standardOutput = output; process.standardError = errors
        try process.run()
        let deadline = Date().addingTimeInterval(60)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if process.isRunning {
            process.terminate()
            let stopDeadline = Date().addingTimeInterval(2)
            while process.isRunning && Date() < stopDeadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            throw nativeWorkflowError("Backup verification exceeded its deadline. Existing workspaces were not replaced; inspect the selected new destination before retrying.")
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let detail = String(data: try Data(contentsOf: errorURL).prefix(4000), encoding: .utf8) ?? "Invalid backup."
            throw nativeWorkflowError("Backup restore was refused. \(detail)")
        }
        let result = try Data(contentsOf: outputURL)
        guard result.count <= 65536 else { throw nativeWorkflowError("Backup verification returned an oversized manifest.") }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(NativeBackupManifest.self, from: result)
        guard manifest.format == "autocue-studio-database", manifest.schemaVersion == 1,
              manifest.databaseBytes > 0, manifest.databaseBytes <= 256 * 1024 * 1024,
              manifest.tableCounts.values.allSatisfy({ $0 >= 0 }) else {
            throw nativeWorkflowError("Unsupported restored-backup manifest.")
        }
        return manifest
    }
}

extension BackendController {
    var currentWorkspacePath: String { dataPath }
    private var bundledBackendURL: URL {
        Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/backend/studio-backend")
    }

    func saveNativeDatabaseBackup() async throws -> URL? {
        guard !hasPendingNativeOperation, !approvalBusy else {
            throw nativeWorkflowError("Reconcile pending approvals or edits before making a database backup.")
        }
        guard let destination = await chooseNativeZIPDestination(filename: "BOOTH BUDDY database backup.zip", title: "Save private database backup") else { return nil }
        guard !hasPendingNativeOperation, !approvalBusy else {
            throw nativeWorkflowError("Reconcile pending changes before saving a backup.")
        }
        let response = try await nativeRequest("api/studio/backup", body: [:])
        guard let encoded = response["bundle_base64"] as? String, let data = Data(base64Encoded: encoded), data.count <= 64 * 1024 * 1024,
              let expected = response["manifest"] as? [String: Any] else {
            throw nativeWorkflowError("Backup response is incomplete. Nothing was saved.")
        }
        let expectedData = try JSONSerialization.data(withJSONObject: expected)
        let manifest = try Self.decodeNative(NativeBackupManifest.self, expectedData)
        let executable = bundledBackendURL
        try await Task.detached(priority: .utility) {
            let temporary = try canonicalNativeFileURL(FileManager.default.temporaryDirectory.appendingPathComponent("BOOTH-BUDDY-backup-check-\(UUID().uuidString)", isDirectory: true))
            try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            defer { try? FileManager.default.removeItem(at: temporary) }
            let archive = temporary.appendingPathComponent("backup.zip")
            try data.write(to: archive, options: .atomic)
            let checked = try NativeBackupTool.restore(executable: executable, archive: archive, destination: temporary.appendingPathComponent("verified"))
            guard checked == manifest else { throw nativeWorkflowError("The downloaded backup did not match its manifest.") }
        }.value
        try writeNativeZIP(data, to: destination)
        return destination
    }

    func restoreNativeDatabaseBackup() async throws -> URL? {
        guard !hasPendingNativeOperation, !approvalBusy, let window = NSApp.keyWindow else {
            throw nativeWorkflowError("Reconcile pending native changes before restoring a backup.")
        }
        let openPanel = NSOpenPanel()
        openPanel.title = "Choose a BOOTH BUDDY database backup"
        openPanel.allowedContentTypes = [.zip]
        openPanel.allowsMultipleSelection = false
        if ProcessInfo.processInfo.arguments.contains("--synthetic-data") { openPanel.directoryURL = URL(fileURLWithPath: dataPath) }
        let archive: URL? = await withCheckedContinuation { continuation in
            openPanel.beginSheetModal(for: window) { response in continuation.resume(returning: response == .OK ? openPanel.url : nil) }
        }
        guard let archive else { return nil }
        let panel = NSSavePanel()
        panel.title = "Choose a new restore folder name"
        panel.nameFieldStringValue = "BOOTH BUDDY Restored"
        panel.prompt = "Restore"
        panel.canCreateDirectories = true
        if ProcessInfo.processInfo.arguments.contains("--synthetic-data") { panel.directoryURL = URL(fileURLWithPath: dataPath) }
        let destination: URL? = await withCheckedContinuation { continuation in
            panel.beginSheetModal(for: window) { response in continuation.resume(returning: response == .OK ? panel.url : nil) }
        }
        guard let destination else { return nil }
        let canonical = try canonicalNativeFileURL(destination)
        let executable = bundledBackendURL
        _ = try await Task.detached(priority: .utility) {
            try NativeBackupTool.restore(executable: executable, archive: archive, destination: canonical)
        }.value
        return canonical
    }

    func switchToRestoredWorkspace(_ location: URL) throws {
        guard !hasPendingNativeOperation, !approvalBusy,
              FileManager.default.fileExists(atPath: location.appendingPathComponent("library.sqlite3").path),
              location == (try canonicalNativeFileURL(location)) else {
            throw nativeWorkflowError("Only a verified restored workspace can be opened; reconcile pending changes first.")
        }
        stop()
        requestedWorkspacePath = location.path
        if !ProcessInfo.processInfo.arguments.contains("--ui-testing"), ProcessInfo.processInfo.environment["AUTOCUE_DESKTOP_DATA"] == nil {
            UserDefaults.standard.set(location.path, forKey: "booth-buddy-active-workspace")
        }
        closing = false; failing = false; attempts = 0
        workflowJournal = nil; workflowJournalError = ""
        nativeWorkspaceRevision += 1
        workspaceSelectionRevision += 1
        start()
    }
}

struct NativeWorkspaceRecoveryView: View {
    @ObservedObject var controller: BackendController
    @State private var busy = false
    @State private var message = ""
    @State private var restoredLocation: URL?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Backup and recovery").font(.headline)
            Text("Private database backups include crates, rules, settings and durable receipts. Audio, model weights, exported playlists and native pending requests are not included. Keep backups private; they are not encrypted.")
                .font(.caption).foregroundStyle(.secondary)
            Text(controller.currentWorkspacePath).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
            Button("Save private database backup…") {
                busy = true; message = ""
                Task {
                    defer { busy = false }
                    do {
                        if let url = try await controller.saveNativeDatabaseBackup() { message = "Saved verified database backup: \(url.path)" }
                        else { message = "Backup canceled." }
                    } catch { message = error.localizedDescription }
                }
            }
            .accessibilityIdentifier("native-backup-save").disabled(busy || controller.hasPendingNativeOperation || controller.approvalBusy)
            Button("Restore backup to a new folder…") {
                busy = true; message = ""; restoredLocation = nil
                Task {
                    defer { busy = false }
                    do {
                        if let location = try await controller.restoreNativeDatabaseBackup() {
                            restoredLocation = location
                            message = "Verified restore created at \(location.path). Your current workspace is unchanged."
                        } else { message = "Restore canceled." }
                    } catch { message = error.localizedDescription }
                }
            }
            .accessibilityIdentifier("native-backup-restore").disabled(busy || controller.hasPendingNativeOperation || controller.approvalBusy)
            if let restoredLocation {
                Button("Open restored workspace") {
                    do { try controller.switchToRestoredWorkspace(restoredLocation) }
                    catch { message = error.localizedDescription }
                }
                .accessibilityIdentifier("native-backup-open-restored").disabled(busy || controller.hasPendingNativeOperation)
            }
            if busy { ProgressView("Verifying private backup evidence…") }
            if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled).accessibilityIdentifier("native-backup-message") }
        }
        .padding(16).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct NativeRequestIntakeView: View {
    @Binding var eventJSON: String
    @State private var expanded = false
    @State private var source = ""
    @State private var format = "lines"
    @State private var category = "must_play"
    @State private var rows: [NativeRequestImportRow] = []
    @State private var message = ""

    private func invalidate() { rows = []; message = "" }
    private func chooseFile() {
        guard let window = NSApp.keyWindow else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.plainText, .commaSeparatedText]
        panel.prompt = "Review request file"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                let handle = try FileHandle(forReadingFrom: url)
                defer { try? handle.close() }
                let data = try handle.read(upToCount: 65537) ?? Data()
                guard data.count <= 65536, let text = String(data: data, encoding: .utf8) else { throw nativeWorkflowError("Choose a UTF-8 request file no larger than 64 KiB.") }
                source = text; rows = []; message = ""
            } catch { rows = []; message = error.localizedDescription }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(expanded ? "Hide client-request intake" : "Import client requests") { expanded.toggle() }
                .accessibilityIdentifier("request-intake-toggle")
            if expanded {
                Text("Paste titles, or choose a UTF-8 CSV/TSV file. Preview and review before adding to this event draft. No library or saved crate is changed.")
                    .font(.caption).foregroundStyle(.secondary)
                Picker("Input format", selection: $format) {
                    Text("One title per line").tag("lines"); Text("CSV with headers").tag("csv"); Text("TSV with headers").tag("tsv")
                }.accessibilityIdentifier("request-intake-format")
                Picker("Default category", selection: $category) {
                    Text("Must play").tag("must_play"); Text("Do not play").tag("do_not_play"); Text("Play if possible").tag("play_if_possible")
                }.accessibilityIdentifier("request-intake-category")
                Text("CSV/TSV columns: title, artist, version, category. Only title is required. Category values: must_play, do_not_play, play_if_possible. Unknown columns are refused. Artist/title splits are never guessed.")
                    .font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $source).font(.system(.body, design: .monospaced)).frame(height: 110)
                    .accessibilityLabel("Client request source text").accessibilityIdentifier("request-intake-source")
                HStack {
                    Button("Choose request file…", action: chooseFile)
                    Button("Preview requests") {
                        rows = []; message = ""
                        do { rows = try NativeRequestIntake.parse(source, format: format, category: category) }
                        catch { message = error.localizedDescription }
                    }.disabled(source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty).accessibilityIdentifier("request-intake-preview")
                }
                if !rows.isEmpty {
                    Text("\(rows.count) requests to review").font(.headline).accessibilityIdentifier("request-intake-count")
                    Text("Edit missing artists and exact versions, and check categories. Repeated requests retain their order. These rows are not matched or resolved; Generate alternatives uses the existing planner resolver and reports conflicts or missing requirements.")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach($rows) { $row in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Source record \(row.sourceRow)").font(.caption2).foregroundStyle(.secondary)
                            TextField("Request title", text: $row.title).accessibilityIdentifier("intake-title-\(row.sourceRow)")
                            HStack {
                                TextField("Artist (review if blank)", text: $row.artist).accessibilityIdentifier("intake-artist-\(row.sourceRow)")
                                TextField("Exact version (optional)", text: $row.version).accessibilityIdentifier("intake-version-\(row.sourceRow)")
                            }
                            HStack {
                                Picker("Category", selection: $row.category) {
                                    Text("Must play").tag("must_play"); Text("Do not play").tag("do_not_play"); Text("Play if possible").tag("play_if_possible")
                                }
                                Button("Remove request") { let sourceRow = row.sourceRow; rows.removeAll { $0.sourceRow == sourceRow } }
                                    .accessibilityIdentifier("intake-remove-\(row.sourceRow)")
                            }
                        }.textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Button("Add reviewed requests to event draft") {
                            do {
                                let count = rows.count
                                eventJSON = try NativeRequestIntake.append(rows, to: eventJSON)
                                rows = []; message = "Added \(count) reviewed requests to the event draft. Generate alternatives to check matches and conflicts. Nothing was saved to a crate."
                            } catch { message = error.localizedDescription }
                        }.accessibilityIdentifier("request-intake-apply")
                        Button("Discard import preview") { invalidate() }.accessibilityIdentifier("request-intake-discard")
                    }
                }
                if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled).accessibilityIdentifier("request-intake-message") }
            }
        }
        .onChange(of: source) { _ in invalidate() }
        .onChange(of: format) { _ in invalidate() }
        .onChange(of: category) { _ in invalidate() }
    }
}

struct NativeFileConstraints: View {
    let controller: BackendController
    @Binding var policyJSON: String
    @State private var expanded = false
    @State private var query = ""
    @State private var tracks: [CatalogTrack] = []
    @State private var labels: [String: String] = [:]
    @State private var selected = ""
    @State private var position = 1
    @State private var loading = false
    @State private var message = ""
    private var policy: [String: Any] { (try? JSONSerialization.jsonObject(with: Data(policyJSON.utf8))) as? [String: Any] ?? [:] }
    private var constraints: [String] {
        let pins = policy["pinned"] as? [[String: Any]] ?? []
        return Set((policy["must_include"] as? [String] ?? []) + (policy["exclude"] as? [String] ?? []) + pins.compactMap { $0["file_id"] as? String }).sorted()
    }
    private func describe(_ id: String) -> String {
        var values: [String] = []
        if (policy["must_include"] as? [String] ?? []).contains(id) { values.append("Required") }
        if (policy["exclude"] as? [String] ?? []).contains(id) { values.append("Excluded") }
        for pin in policy["pinned"] as? [[String: Any]] ?? [] where pin["file_id"] as? String == id {
            if let slot = pin["position"] as? Int { values.append("Pinned at \(slot + 1)") }
        }
        return values.joined(separator: " · ")
    }
    private func apply(_ action: String, id: String) {
        do { policyJSON = try NativePlannerDraft.update(policyJSON, fileID: id, action: action, position: position); message = "" }
        catch { message = error.localizedDescription }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button(expanded ? "Hide exact-file constraints" : "Exact-file requirements, exclusions and pins") { expanded.toggle() }
                .accessibilityIdentifier("planner-file-constraints")
            if expanded {
                Text("Choose an indexed file, then set its role in this draft. Pinned positions start at 1. Files still need to be present in your candidate search and pass backend validation.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    TextField("Find a file by title, artist or version", text: $query).textFieldStyle(.roundedBorder)
                        .accessibilityIdentifier("constraint-search")
                    Button("Find files") {
                        let submitted = query
                        loading = true; message = ""; tracks = []; selected = ""
                        Task {
                            defer { loading = false }
                            do {
                                let result = try await controller.searchCatalog(query: submitted, cleanOnly: false)
                                guard query == submitted else { return }
                                tracks = result.tracks; selected = tracks.first?.id ?? ""
                                for track in tracks { labels[track.id] = "\(track.title) — \(track.artist)" }
                                message = result.total == 0 ? "No matching indexed files." : "\(result.tracks.count) of \(result.total) matching files shown. Narrow the search if needed."
                            } catch { message = error.localizedDescription }
                        }
                    }.disabled(loading).accessibilityIdentifier("constraint-find")
                }
                if loading { ProgressView("Finding indexed files…") }
                if !tracks.isEmpty {
                    Picker("Exact file", selection: $selected) {
                        ForEach(tracks) { track in Text("\(track.title) — \(track.artist) · \(track.path)").tag(track.id) }
                    }.accessibilityIdentifier("constraint-file")
                    HStack {
                        Button("Require file") { apply("require", id: selected) }.accessibilityIdentifier("constraint-require")
                        Button("Exclude file") { apply("exclude", id: selected) }.accessibilityIdentifier("constraint-exclude")
                    }.disabled(selected.isEmpty)
                    HStack {
                        Stepper("Pin position: \(position)", value: $position, in: 1...60).accessibilityIdentifier("constraint-position")
                        Button("Pin file") { apply("pin", id: selected) }.disabled(selected.isEmpty).accessibilityIdentifier("constraint-pin")
                    }
                }
                ForEach(constraints, id: \.self) { id in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(labels[id] ?? "Exact file \(id)").font(.caption.weight(.semibold))
                            Text(describe(id)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Clear file constraints") { apply("clear", id: id) }.accessibilityIdentifier("constraint-clear-\(id)")
                    }
                }
                if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled).accessibilityIdentifier("constraint-message") }
            }
        }
    }
}

struct NativePlanningDetails: View {
    @Binding var eventJSON: String
    @Binding var policyJSON: String
    @State private var expanded = false
    @State private var category = "must_play"
    @State private var title = ""
    @State private var artist = ""
    @State private var version = ""
    @State private var phaseKind = "generic"
    @State private var phaseLabel = ""
    @State private var phaseMinutes = 15.0

    private func object(_ text: String) -> [String: Any]? {
        guard let data = text.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
    private func encoded(_ value: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return nil }
        return String(data: data, encoding: .utf8)
    }
    private var lists: [String: Any]? {
        guard let event = object(eventJSON) else { return nil }
        if let value = event["music_lists"] { return value as? [String: Any] }
        return [:]
    }
    private var requests: [[String: Any]]? {
        guard let lists else { return nil }
        if let value = lists[category] { return value as? [[String: Any]] }
        return []
    }
    private var phases: [[String: Any]]? {
        guard let policy = object(policyJSON) else { return nil }
        if let value = policy["phases"] { return value as? [[String: Any]] }
        return []
    }
    private func updateRequests(_ values: [[String: Any]]) {
        guard var event = object(eventJSON), var lists else { return }
        lists[category] = values; event["music_lists"] = lists
        if let text = encoded(event) { eventJSON = text }
    }
    private func updatePhases(_ values: [[String: Any]]) {
        guard var policy = object(policyJSON) else { return }
        if values.isEmpty { policy.removeValue(forKey: "phases") } else { policy["phases"] = values }
        if let text = encoded(policy) { policyJSON = text }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(expanded ? "Hide event requests and phases" : "Event requests and phases") { expanded.toggle() }
                .accessibilityIdentifier("native-planning-details")
            if expanded {
                Text("Event requests").font(.headline)
                Picker("Request category", selection: $category) {
                    Text("Must play").tag("must_play")
                    Text("Do not play").tag("do_not_play")
                    Text("Play if possible").tag("play_if_possible")
                }
                HStack {
                    TextField("Song title", text: $title).accessibilityIdentifier("event-request-title")
                    TextField("Artist", text: $artist).accessibilityIdentifier("event-request-artist")
                    TextField("Version (optional)", text: $version).accessibilityIdentifier("event-request-version")
                }
                .textFieldStyle(.roundedBorder)
                Button("Add event request") {
                    guard var values = requests else { return }
                    var request = ["title": title.trimmingCharacters(in: .whitespacesAndNewlines), "artist": artist]
                    if !version.isEmpty { request["version"] = version }
                    values.append(request); updateRequests(values); title = ""; artist = ""; version = ""
                }
                .accessibilityIdentifier("event-request-add").disabled(requests == nil || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if let requests {
                    ForEach(Array(requests.enumerated()), id: \.offset) { index, request in
                        HStack {
                            Text("\(request["title"] as? String ?? "Unknown title") — \(request["artist"] as? String ?? "Unknown artist") \(request["version"] as? String ?? "")")
                                .font(.caption)
                            Spacer()
                            Button("Remove request") { var values = requests; values.remove(at: index); updateRequests(values) }
                                .accessibilityIdentifier("event-request-remove-\(index)")
                        }
                    }
                } else { Text("Correct the existing request JSON before editing this list.").font(.caption).foregroundStyle(.orange) }
                Divider()
                Text("Ordered phases").font(.headline)
                Text("Phase budgets must fit the overall target and hard constraints. Removing a phase is an explicit draft change; existing exact-file requirements stay in the remaining policy.")
                    .font(.caption).foregroundStyle(.secondary)
                Picker("Phase", selection: $phaseKind) {
                    Text("Ceremony").tag("ceremony"); Text("Cocktail").tag("cocktail"); Text("Dinner").tag("dinner")
                    Text("First dance").tag("first_dance"); Text("Open dancing").tag("open_dancing"); Text("Other").tag("generic")
                }
                HStack {
                    TextField("Phase label", text: $phaseLabel).textFieldStyle(.roundedBorder).accessibilityIdentifier("phase-label")
                    TextField("Minutes", value: $phaseMinutes, format: .number).textFieldStyle(.roundedBorder).accessibilityIdentifier("phase-minutes")
                }
                Button("Add phase") {
                    guard var values = phases else { return }
                    values.append(["phase": phaseKind, "label": phaseLabel, "target_minutes": phaseMinutes, "tolerance_minutes": 0])
                    updatePhases(values); phaseLabel = ""
                }
                .accessibilityIdentifier("phase-add").disabled(phases == nil || (phases?.count ?? 12) >= 12 || phaseLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !phaseMinutes.isFinite || phaseMinutes <= 0)
                if let phases {
                    ForEach(Array(phases.enumerated()), id: \.offset) { index, phase in
                        HStack {
                            Text("\(index + 1). \(phase["label"] as? String ?? phase["phase"] as? String ?? "Phase")")
                                .font(.caption)
                            TextField("Phase minutes", value: Binding(get: { (phase["target_minutes"] as? NSNumber)?.doubleValue ?? 0 }, set: { value in
                                guard value.isFinite, value > 0 else { return }
                                var values = phases; values[index]["target_minutes"] = value; updatePhases(values)
                            }), format: .number).textFieldStyle(.roundedBorder).frame(maxWidth: 100).accessibilityIdentifier("phase-budget-\(index)")
                            Spacer()
                            Button { var values = phases; values.swapAt(index, index - 1); updatePhases(values) } label: { Image(systemName: "arrow.up") }
                                .accessibilityLabel("Move phase \(index + 1) up").disabled(index == 0)
                            Button { var values = phases; values.swapAt(index, index + 1); updatePhases(values) } label: { Image(systemName: "arrow.down") }
                                .accessibilityLabel("Move phase \(index + 1) down").disabled(index + 1 == phases.count)
                            Button("Remove phase") { var values = phases; values.remove(at: index); updatePhases(values) }
                                .accessibilityIdentifier("phase-remove-\(index)")
                        }
                    }
                } else { Text("Correct the existing phase JSON before editing phases.").font(.caption).foregroundStyle(.orange) }
            }
        }
    }
}

extension BackendController {
    func nativeAuditionBytes(_ track: NativePlanTrack) async throws -> Data {
        guard started, !closing, let origin,
              UUID(uuidString: track.id)?.uuidString.lowercased() == track.id else { throw nativeWorkflowError("Choose an exact file from the current plan after the local workspace is ready.") }
        let url = origin.appendingPathComponent("api/studio/audition").appendingPathComponent(track.id)
        func fetch(_ range: String, maximum: Int) async throws -> (Data, HTTPURLResponse) {
            var request = URLRequest(url: url)
            request.setValue(origin.absoluteString, forHTTPHeaderField: "Origin")
            request.setValue(range, forHTTPHeaderField: "Range")
            let result = try await NativeAuditionDownload.load(request, maximum: maximum)
            try Task.checkCancellation()
            guard result.1.url == url, result.1.statusCode == 206 else {
                throw nativeWorkflowError(Self.responseMessage(result.0, fallback: "The indexed audio could not be verified. Rescan and generate again."))
            }
            return result
        }
        let (probe, response) = try await fetch("bytes=0-0", maximum: 65536)
        let size = try NativeAuditionVerification.size(status: response.statusCode, etag: response.value(forHTTPHeaderField: "ETag"), range: response.value(forHTTPHeaderField: "Content-Range"), expectedHash: track.contentHash)
        guard probe.count == 1 else { throw nativeWorkflowError("The local audio service returned an unexpected probe.") }
        let (data, full) = try await fetch("bytes=0-\(size - 1)", maximum: size)
        try NativeAuditionVerification.verify(data, size: size, status: full.statusCode, etag: full.value(forHTTPHeaderField: "ETag"), range: full.value(forHTTPHeaderField: "Content-Range"), expectedHash: track.contentHash)
        return data
    }

    var canOpenDefaultWorkspace: Bool {
        ProcessInfo.processInfo.environment["AUTOCUE_DESKTOP_DATA"] == nil &&
        !ProcessInfo.processInfo.arguments.contains("--synthetic-data") &&
        UserDefaults.standard.string(forKey: "booth-buddy-active-workspace") != nil
    }
    func openDefaultWorkspace() {
        guard canOpenDefaultWorkspace, !hasPendingNativeOperation, !approvalBusy else { return }
        stop()
        UserDefaults.standard.removeObject(forKey: "booth-buddy-active-workspace")
        requestedWorkspacePath = nil; closing = false; failing = false; attempts = 0
        workflowJournal = nil; workflowJournalError = ""
        workspaceSelectionRevision += 1; nativeWorkspaceRevision += 1
        start()
    }
}

extension BackendController {
    func saveRedactedNativeSupportReport() async throws -> URL? {
        guard let window = NSApp.keyWindow else { return nil }
        let panel = NSSavePanel()
        panel.title = "Save redacted support report"
        panel.nameFieldStringValue = "BOOTH BUDDY support report.json"
        panel.allowedContentTypes = [.json]
        if ProcessInfo.processInfo.arguments.contains("--ui-testing"), ProcessInfo.processInfo.arguments.contains("--synthetic-data") {
            panel.directoryURL = URL(fileURLWithPath: dataPath)
        }
        let destination: URL? = await withCheckedContinuation { continuation in
            panel.beginSheetModal(for: window) { response in continuation.resume(returning: response == .OK ? panel.url : nil) }
        }
        guard let destination else { return nil }
        let state = try? await nativeRequest("api/studio/state")
        let health = state?["health"] as? [String: Any]
        let counts = health?["counts"] as? [String: Int] ?? [:]
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "other"
        #endif
        let report: [String: Any] = [
            "schema_version": 1, "product": "BOOTH BUDDY", "profile": "macos-local",
            "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
            "os": ProcessInfo.processInfo.operatingSystemVersionString, "architecture": architecture,
            "generated_at": ISO8601DateFormatter().string(from: Date()),
            "backend_state_available": state != nil, "indexed_counts": counts,
            "pending_native_approval": workflowJournal?.pending != nil,
            "pending_native_edit": workflowJournal?.pendingEdit != nil,
            "native_recovery_available": workflowJournal != nil,
            "redaction": "No local paths, audio, track/crate/event names, notes, tokens, account identifiers, model settings, environment variables or raw error logs are included. Review before sharing; nothing is uploaded automatically."
        ]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        try writeNativeZIP(data, to: destination)
        return destination
    }
}

struct NativeSupportView: View {
    let controller: BackendController
    @State private var message = ""
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Help and support").font(.headline)
            Text("Start with a selected folder, review a generated set, approve its exact files, then preview and save the verified ZIP. Extract the ZIP before importing the playlist. Back up your private app database before moving workspaces.")
                .font(.subheadline).foregroundStyle(.secondary)
            Text("Local core functions do not require a provider account or AI model. Connected streaming, automatic DJ-software control and paid licensing are not enabled in this development build.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Save redacted support report…") {
                busy = true; message = ""
                Task {
                    defer { busy = false }
                    do {
                        if let url = try await controller.saveRedactedNativeSupportReport() { message = "Support report saved: \(url.path). Review it before sharing." }
                        else { message = "Support report canceled." }
                    } catch { message = error.localizedDescription }
                }
            }
            .accessibilityIdentifier("native-support-save").disabled(busy)
            if !message.isEmpty { Text(message).font(.caption).textSelection(.enabled).accessibilityIdentifier("native-support-message") }
        }
        .padding(16).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
    }
}
