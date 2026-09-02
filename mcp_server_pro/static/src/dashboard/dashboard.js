/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, onWillUnmount, useState, useRef, onMounted } from "@odoo/owl";

class McpDashboard extends Component {
    static template = "mcp_server_pro.Dashboard";
    static props = {
        action: { type: Object, optional: true },
        actionId: { type: [Number, Boolean], optional: true },
        "*": true,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvasRef = useRef("volumeCanvas");

        this.state = useState({
            loading: true,
            window: 24,
            data: null,
            error: null,
            autoRefresh: true,
            lastRefresh: null,
        });

        onWillStart(async () => {
            await this.load();
        });

        onMounted(() => {
            this._timer = setInterval(() => {
                if (this.state.autoRefresh) {
                    this.load(true);
                }
            }, 15000);
        });

        onWillUnmount(() => {
            if (this._timer) {
                clearInterval(this._timer);
            }
        });
    }

    async load(silent = false) {
        if (!silent) {
            this.state.loading = true;
        }
        try {
            const data = await this.orm.call(
                "mcp.dashboard",
                "get_dashboard",
                [],
                { tenant_id: false, hours: this.state.window }
            );
            this.state.data = data;
            this.state.error = null;
            this.state.lastRefresh = new Date().toLocaleTimeString();
            this.state.loading = false;
            // draw after render
            setTimeout(() => this.drawVolume(), 50);
        } catch (e) {
            this.state.error = e.message || "Failed to load dashboard data.";
            this.state.loading = false;
        }
    }

    setWindow(h) {
        this.state.window = h;
        this.load();
    }

    toggleAuto() {
        this.state.autoRefresh = !this.state.autoRefresh;
    }

    get counters() {
        return this.state.data?.counters || {};
    }

    get statusMix() {
        const mix = this.state.data?.status_mix || [];
        const total = mix.reduce((a, b) => a + b.count, 0) || 1;
        const colorMap = {
            ok: "var(--mcp-ok)",
            denied: "var(--mcp-warn)",
            error: "var(--mcp-err)",
            rate_limited: "var(--mcp-rl)",
            unknown: "var(--mcp-muted)",
        };
        return mix.map((m) => ({
            ...m,
            pct: Math.round((m.count / total) * 100),
            color: colorMap[m.status] || "var(--mcp-muted)",
        }));
    }

    get topTools() {
        const tools = this.state.data?.top_tools || [];
        const max = Math.max(...tools.map((t) => t.count), 1);
        return tools.map((t) => ({ ...t, pct: Math.round((t.count / max) * 100) }));
    }

    get slowestTools() {
        return this.state.data?.slowest_tools || [];
    }

    get busyKeys() {
        const keys = this.state.data?.busy_keys || [];
        const max = Math.max(...keys.map((k) => k.count), 1);
        return keys.map((k) => ({ ...k, pct: Math.round((k.count / max) * 100) }));
    }

    get recent() {
        return (this.state.data?.recent || []).map((r) => ({
            ...r,
            time: r.create_date ? r.create_date.slice(11, 19) : "",
        }));
    }

    get tenants() {
        return this.state.data?.tenants || [];
    }

    statusClass(status) {
        return {
            ok: "mcp-pill mcp-pill--ok",
            denied: "mcp-pill mcp-pill--warn",
            error: "mcp-pill mcp-pill--err",
            rate_limited: "mcp-pill mcp-pill--rl",
        }[status] || "mcp-pill";
    }

    drawVolume() {
        const canvas = this.canvasRef.el;
        if (!canvas) return;
        const pts = this.state.data?.volume?.points || [];
        const dpr = window.devicePixelRatio || 1;
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        const ctx = canvas.getContext("2d");
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, w, h);

        if (!pts.length) {
            ctx.fillStyle = "rgba(148,163,184,.7)";
            ctx.font = "12px monospace";
            ctx.fillText("no traffic in window", 12, h / 2);
            return;
        }

        const counts = pts.map((p) => p.count);
        const max = Math.max(...counts, 1);
        const pad = 8;
        const bw = (w - pad * 2) / pts.length;

        // gradient fill bars
        pts.forEach((p, i) => {
            const bh = ((p.count / max) * (h - pad * 2));
            const x = pad + i * bw;
            const y = h - pad - bh;
            const grad = ctx.createLinearGradient(0, y, 0, h - pad);
            grad.addColorStop(0, "rgba(14,165,233,.9)");
            grad.addColorStop(1, "rgba(14,165,233,.12)");
            ctx.fillStyle = grad;
            const bwi = Math.max(bw - 3, 1);
            ctx.beginPath();
            const r = Math.min(3, bwi / 2);
            ctx.roundRect(x, y, bwi, bh, [r, r, 0, 0]);
            ctx.fill();
        });
    }

    openAudit() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mcp.audit.log",
            views: [[false, "list"], [false, "form"]],
            name: "Audit Logs",
        });
    }

    openKeys() {
        this.action.doAction("mcp_server_pro.action_mcp_api_key");
    }
}

registry.category("actions").add("mcp_server_pro.dashboard", McpDashboard);

export { McpDashboard };
