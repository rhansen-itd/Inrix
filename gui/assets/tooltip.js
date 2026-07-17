// Client-side formatter for the time-of-day RangeSlider tooltip (ROADMAP Item 13).
// Referenced by dcc.RangeSlider's tooltip.transform = "hourToClock", so a handle
// at 13.5 reads as "1:30 PM" instead of the raw hour. Mirrors the Python
// _hour_label in gui/app.py (24 == end of day == midnight).
window.dccFunctions = window.dccFunctions || {};
window.dccFunctions.hourToClock = function (value) {
    if (value >= 24) return "12:00 AM";
    var h = Math.floor(value);
    var m = Math.round((value - h) * 60);
    if (m === 60) { h += 1; m = 0; }
    var ampm = h >= 12 ? "PM" : "AM";
    var hr = h % 12;
    if (hr === 0) hr = 12;
    var mm = m < 10 ? "0" + m : "" + m;
    return hr + ":" + mm + " " + ampm;
};
