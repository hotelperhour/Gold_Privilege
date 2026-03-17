/**
 * service_admin.js
 * Dynamically show/hide the correct fieldset sections and inline quota columns
 * based on the selected category and delivery_type in the Service admin form.

 */

(function () {
    'use strict';

    // Fieldset class selectors (match 'classes' in admin fieldsets)
    var SECTIONS = {
        airtime: '.section-airtime',
        data:    '.section-data',
        voucher: '.section-voucher',
    };

    // Quota inline column ids (Django renders them as column headers)
    // We'll show/hide the TD cells in each inline row
    var QUOTA_FIELDS = {
        airtime: 'id_plan_quotas-__prefix__-monthly_allowance',
        data:    'id_plan_quotas-__prefix__-monthly_data_gb',
        voucher: 'id_plan_quotas-__prefix__-monthly_voucher_count',
    };

    function getCategory() {
        var el = document.getElementById('id_category');
        return el ? el.value : '';
    }

    function getDeliveryType() {
        var el = document.getElementById('id_delivery_type');
        return el ? el.value : '';
    }

    function updateVisibility() {
        var cat      = getCategory();
        var delivery = getDeliveryType();
        var isData    = cat === 'DATA';
        var isAirtime = cat === 'AIRTIME';
        var isVoucher = delivery === 'MANUAL_CODE';

        // Show/hide fieldset sections
        Object.keys(SECTIONS).forEach(function (key) {
            var el = document.querySelector(SECTIONS[key]);
            if (!el) return;
            var fieldset = el.closest('fieldset');
            if (!fieldset) return;
            if (key === 'airtime') fieldset.style.display = isAirtime ? '' : 'none';
            if (key === 'data')    fieldset.style.display = isData    ? '' : 'none';
            if (key === 'voucher') fieldset.style.display = isVoucher ? '' : 'none';
        });

        // Update inline quota column headers and cells
        updateInlineColumns(isAirtime, isData, isVoucher);
    }

    function updateInlineColumns(isAirtime, isData, isVoucher) {
        // Column header TH elements
        var headers = document.querySelectorAll('.tabular th');
        headers.forEach(function (th) {
            var text = th.textContent.trim().toLowerCase();
            if (text.includes('monthly allowance')) {
                th.style.display = isAirtime ? '' : 'none';
            }
            if (text.includes('monthly data gb') || text.includes('data gb')) {
                th.style.display = isData ? '' : 'none';
            }
            if (text.includes('voucher count') || text.includes('monthly voucher')) {
                th.style.display = isVoucher ? '' : 'none';
            }
        });

        // Data cells in inline rows
        var rows = document.querySelectorAll('.tabular tr.dynamic-plan_quotas, .tabular tr.has_original');
        rows.forEach(function (row) {
            var cells = row.querySelectorAll('td');
            // The order matches fields=(...) in the inline:
            // 0=plan, 1=monthly_allowance, 2=monthly_data_gb, 3=monthly_voucher_count, 4=delete
            if (cells[1]) cells[1].style.display = isAirtime ? '' : 'none';
            if (cells[2]) cells[2].style.display = isData    ? '' : 'none';
            if (cells[3]) cells[3].style.display = isVoucher ? '' : 'none';
        });
    }

    function addHintBanner() {
        var existingBanner = document.getElementById('gp-quota-hint');
        if (existingBanner) existingBanner.remove();

        var cat      = getCategory();
        var delivery = getDeliveryType();
        var msg      = '';

        if (cat === 'AIRTIME') {
            msg = '💰 <strong>Airtime service:</strong> Set the <em>Monthly Allowance (₦)</em> column in Plan Quotas below.';
        } else if (cat === 'DATA') {
            msg = '📶 <strong>Data service:</strong> Set the <em>Monthly Data GB</em> column in Plan Quotas below.';
        } else if (delivery === 'MANUAL_CODE') {
            msg = '🎟️ <strong>Voucher service:</strong> Set the <em>Monthly Voucher Count</em> column in Plan Quotas below.';
        }

        if (!msg) return;

        var banner = document.createElement('div');
        banner.id = 'gp-quota-hint';
        banner.style.cssText = (
            'background:#fff8e1;border:1px solid #E5AD04;border-radius:6px;' +
            'padding:10px 16px;margin:12px 0;font-size:13px;color:#5a4100;'
        );
        banner.innerHTML = msg;

        var inlineGroup = document.querySelector('.inline-group');
        if (inlineGroup) {
            inlineGroup.parentNode.insertBefore(banner, inlineGroup);
        }
    }

    function init() {
        var categoryEl    = document.getElementById('id_category');
        var deliveryEl    = document.getElementById('id_delivery_type');

        if (!categoryEl || !deliveryEl) return;

        categoryEl.addEventListener('change', function () {
            updateVisibility();
            addHintBanner();
        });
        deliveryEl.addEventListener('change', function () {
            updateVisibility();
            addHintBanner();
        });

        // Run on load
        updateVisibility();
        addHintBanner();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();