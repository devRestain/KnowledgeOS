-- KnowledgeOS Mac local navigation helper.
-- Requests existing notes through Obsidian's obsidian://open URI; this is not a capture writer.
-- Public pure handler: makeOpenURI(actionName, notePath) -> URI text.
-- Actions: mobile (default), home, today, note <relative .md path>.
-- The vault is intentionally fixed to KnowledgeHub. No file creation, Vault write,
-- settings change, clipboard use, UI automation, shell command or network access.
--
-- LaunchServices hands the URI to Obsidian asynchronously. Returning a URI records
-- the request, not an acknowledgement that Obsidian opened or displayed the note.

use framework "Foundation"
use scripting additions

property defaultVaultName : "KnowledgeHub"

on run argv
    set requestedAction to "mobile"
    set requestedNote to missing value

    if (count of argv) > 2 then error "Expected an action and at most one relative Markdown path."
    if (count of argv) > 0 then set requestedAction to (item 1 of argv) as text
    if (count of argv) > 1 then set requestedNote to (item 2 of argv) as text

    set targetURI to makeOpenURI(requestedAction, requestedNote)
    open location targetURI
    return targetURI
end run

-- Pure URI builder used by native loaded-script checks.
-- Pass missing value for notePath with mobile, home and today.
on makeOpenURI(actionName, notePath)
    if actionName is missing value then error "Missing action."

    set actionText to actionName as text
    set relativePath to ""

    if actionText is "mobile" then
        if notePath is not missing value then error "The mobile action does not take a note path."
        set relativePath to "Mobile.md"
    else if actionText is "home" then
        if notePath is not missing value then error "The home action does not take a note path."
        set relativePath to "Home.md"
    else if actionText is "today" then
        if notePath is not missing value then error "The today action does not take a note path."
        set relativePath to todayRelativePath()
    else if actionText is "note" then
        if notePath is missing value then error "The note action requires a relative Markdown path."
        set relativePath to notePath as text
        validateRelativeMarkdownPath(relativePath)
    else
        error "Unsupported action: " & actionText
    end if

    -- Encode each query component separately with only RFC 3986 unreserved
    -- characters. Do not use NSURLQueryItem: it leaves a raw plus sign, which
    -- form-style query decoding may treat as a space.
    return "obsidian://open?vault=" & percentEncodeComponent(defaultVaultName) & "&file=" & percentEncodeComponent(relativePath)
end makeOpenURI

on todayRelativePath()
    set formatter to current application's NSDateFormatter's alloc()'s init()
    set posixLocale to current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX"
    set gregorianCalendar to current application's NSCalendar's calendarWithIdentifier:"gregorian"
    set localTimeZone to current application's NSTimeZone's localTimeZone()
    set nowDate to current application's NSDate's dateWithTimeIntervalSinceNow:0

    if posixLocale is missing value then error "Could not create the en_US_POSIX locale."
    if gregorianCalendar is missing value then error "Could not create the Gregorian calendar."
    if localTimeZone is missing value then error "Could not read the local time zone."
    if nowDate is missing value then error "Could not read the current date."

    formatter's setLocale:posixLocale
    formatter's setCalendar:gregorianCalendar
    formatter's setTimeZone:localTimeZone
    formatter's setDateFormat:"yyyy-MM-dd"

    set dayObject to formatter's stringFromDate:nowDate
    if dayObject is missing value then error "Could not format the local Gregorian date."
    set dayText to dayObject as text
    if dayText is "" then error "The local Gregorian date is empty."

    return "10_Journal/Daily/" & dayText & ".md"
end todayRelativePath

on percentEncodeComponent(sourceText)
    set allowedCharacters to current application's NSCharacterSet's characterSetWithCharactersInString:"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    set sourceString to current application's NSString's stringWithString:(sourceText as text)
    set encodedString to sourceString's stringByAddingPercentEncodingWithAllowedCharacters:allowedCharacters

    if encodedString is missing value then error "Could not percent-encode a URI component."
    return encodedString as text
end percentEncodeComponent

on validateRelativeMarkdownPath(notePath)
    set relativePath to notePath as text
    if relativePath is "" then error "The note action requires a relative Markdown path."
    if relativePath starts with "/" then error "The note path must be relative."
    if relativePath starts with "~" then error "The note path must be relative."
    if relativePath does not end with ".md" then error "The note path must end with .md."

    set savedDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "/"
    set pathParts to text items of relativePath
    set AppleScript's text item delimiters to savedDelimiters

    repeat with pathPart in pathParts
        if (pathPart as text) is ".." then error "The note path must not contain .. segments."
        if (pathPart as text) is "" then error "The note path must not contain empty segments."
    end repeat
end validateRelativeMarkdownPath
