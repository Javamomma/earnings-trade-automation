// Google Apps Script — Earnings sheet I/O.
//
// When `AUTH_TOKEN` is set via Script Properties, every write request
// must include a matching `auth` field in its JSON body. The deployed
// URL alone is then no longer sufficient to mutate the sheet.

function _getAuthToken() {
  return PropertiesService.getScriptProperties().getProperty('AUTH_TOKEN');
}

function _jsonError(msg) {
  return ContentService
    .createTextOutput(JSON.stringify({ error: msg }))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Earnings");
  if (!sheet) return _jsonError("Sheet not found");
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var rows = data.slice(1).map(function (row) {
    var obj = {};
    headers.forEach(function (header, i) { obj[header] = row[i]; });
    return obj;
  });
  if (e.parameter.status && e.parameter.status == "OPEN") {
    rows = rows.filter(function (row) { return row["Result"] == "OPEN"; });
  }
  return ContentService.createTextOutput(JSON.stringify(rows))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Earnings");
  if (!sheet) {
    return ContentService.createTextOutput("None active")
      .setMimeType(ContentService.MimeType.TEXT);
  }

  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (parseErr) {
    return _jsonError("Invalid JSON");
  }

  // Optional shared-secret auth. If AUTH_TOKEN is unset we preserve the
  // legacy unauthenticated behavior for backwards compatibility, but
  // strongly recommend setting it in Script Properties.
  var expected = _getAuthToken();
  if (expected && data.auth !== expected) {
    return _jsonError("Unauthorized");
  }
  delete data.auth;

  if (data.action === "update") {
    var values = sheet.getDataRange().getValues();
    var headers = values[0];
    var keyTicker = data["Ticker"];
    var keyOpenDate = data["Open Date"];
    var updated = false;
    var logMessage = "doPost update. Ticker=" + keyTicker + " OpenDate=" + keyOpenDate;
    for (var i = 1; i < values.length; i++) {
      if (values[i][headers.indexOf("Ticker")] == keyTicker &&
          values[i][headers.indexOf("Open Date")] == keyOpenDate) {
        var colsToUpdate = Math.min(headers.length, 12);
        for (var j = 0; j < colsToUpdate; j++) {
          var header = headers[j];
          if (data[header] !== undefined) {
            sheet.getRange(i + 1, j + 1).setValue(data[header]);
          }
        }
        logMessage += " -> row " + (i + 1) + " updated";
        updated = true;
        break;
      }
    }
    if (!updated) logMessage += " -> no match";
    return ContentService.createTextOutput(logMessage).setMimeType(ContentService.MimeType.TEXT);
  }

  // Create/append
  var values = sheet.getDataRange().getValues();
  var headers = values[0];
  var colsToWrite = Math.min(headers.length, 12);
  var tickerColIndex = headers.indexOf("Ticker");
  if (tickerColIndex === -1) {
    var rowData = headers.slice(0, colsToWrite).map(function (h) { return data[h] || ""; });
    sheet.appendRow(rowData);
    return ContentService.createTextOutput("OK - Appended (no Ticker header)")
      .setMimeType(ContentService.MimeType.TEXT);
  }
  var targetRowIndex = -1;
  for (var i = 1; i < values.length; i++) {
    if (!values[i][tickerColIndex]) { targetRowIndex = i + 1; break; }
  }
  var newRowData = headers.slice(0, colsToWrite).map(function (h) { return data[h] || ""; });
  if (targetRowIndex !== -1) {
    sheet.getRange(targetRowIndex, 1, 1, colsToWrite).setValues([newRowData]);
    return ContentService.createTextOutput("OK - Updated row " + targetRowIndex)
      .setMimeType(ContentService.MimeType.TEXT);
  }
  sheet.appendRow(newRowData);
  return ContentService.createTextOutput("OK - Appended").setMimeType(ContentService.MimeType.TEXT);
}

function doOptions(e) {
  return ContentService.createTextOutput("OK").setMimeType(ContentService.MimeType.TEXT);
}
