import os
import sys

# Asegurar que la raíz del proyecto esté en sys.path al ejecutar streamlit directamente
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import re
from collections import Counter
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlmodel import Session, select

import src.database.repository as repo
from src.database.models import Job, MatchResult
from src.market_engine.analytics import (
    extract_and_normalize_salary,
    extract_detailed_modality,
    get_tech_patterns,
    is_job_chile,
    normalize_role,
)

# Configuración de página Streamlit
st.set_page_config(
    page_title="Estudio de Mercado | Data & Analytics Chile",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilos CSS Catppuccin Dark (Mocha / Macchiato)
st.markdown(
    """
    <style>
    /* Catppuccin Mocha Dark Palette */
    .stApp {
        background-color: #1e1e2e;
        color: #cdd6f4;
    }
    .sidebar .sidebar-content {
        background-color: #181825;
    }
    /* Metric Card Styling */
    .metric-card {
        background-color: #181825;
        border: 1px solid #313244;
        border-radius: 12px;
        padding: 18px 12px;
        text-align: center;
        margin-bottom: 15px;
    }
    .metric-val {
        font-size: 1.9rem;
        font-weight: 700;
        color: #cba6f7; /* Mauve */
    }
    .metric-lbl {
        font-size: 0.85rem;
        color: #a6adc8; /* Subtext */
        margin-top: 4px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    /* Accent text */
    .highlight-green { color: #a6e3a1; font-weight: 600; }
    .highlight-mauve { color: #cba6f7; font-weight: 600; }
    .highlight-gold { color: #f9e2af; font-weight: 600; }
    .highlight-blue { color: #89dceb; font-weight: 600; }

    /* Clean divider */
    hr {
        border: 0;
        height: 1px;
        background: #313244;
        margin: 25px 0;
    }
    </style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60)
def load_data(scope_filter: str = "chile"):
    """Carga y procesa las vacantes desde SQLite DB según el ámbito geográfico ('chile', 'international', 'all')."""
    with Session(repo.engine) as session:
        jobs = session.exec(select(Job)).all()
        matches = session.exec(select(MatchResult)).all()

    if not jobs:
        return None

    total_jobs_db = len(jobs)
    raw_data_jobs = [j for j in jobs if normalize_role(j.title) != "Excluded Non-Data Role"]

    # Filtrar por Ámbito Geográfico
    if scope_filter == "chile":
        valid_data_jobs = [j for j in raw_data_jobs if is_job_chile(j)]
    elif scope_filter == "international":
        valid_data_jobs = [j for j in raw_data_jobs if not is_job_chile(j)]
    else:
        valid_data_jobs = raw_data_jobs

    n_data = len(valid_data_jobs)
    n_noise = total_jobs_db - n_data
    n_base = n_data if n_data else 1

    tier12_ids = {m.job_id for m in matches if m.tier in (1, 2)}
    valid_job_ids = {j.id for j in valid_data_jobs}
    n_fit = sum(1 for m in matches if m.job_id in valid_job_ids and m.tier in (1, 2))

    created_dates = [j.created_at for j in valid_data_jobs if j.created_at]
    first_date = min(created_dates).strftime("%d/%m/%Y") if created_dates else "N/D"
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    roles_counter = Counter([normalize_role(j.title) for j in valid_data_jobs])
    top_roles = [r for r, _ in roles_counter.most_common()]

    # Procesar Salarios usando la función centralizada y validada de analytics
    jobs_with_salary: list[dict] = []
    is_usd_mode = scope_filter == "international"

    for job in valid_data_jobs:
        sal = extract_and_normalize_salary(job)
        if sal:
            if is_usd_mode:
                jobs_with_salary.append({
                    "role": sal["role"],
                    "min_val": sal["usd_min"],
                    "avg_val": sal["usd_avg"],
                    "max_val": sal["usd_max"],
                    "equiv_clp": sal["clp_avg"],
                    "currency": "USD",
                })
            else:
                jobs_with_salary.append({
                    "role": sal["role"],
                    "min_val": sal["clp_min"],
                    "avg_val": sal["clp_avg"],
                    "max_val": sal["clp_max"],
                    "currency": "CLP",
                })

    salaries_by_role: dict[str, list[dict]] = {}
    all_median_salaries = []
    for s in jobs_with_salary:
        salaries_by_role.setdefault(s["role"], []).append(s)
        all_median_salaries.append(s["avg_val"])

    global_salary_median = sorted(all_median_salaries)[len(all_median_salaries)//2] if all_median_salaries else 0

    return {
        "scope": scope_filter,
        "total_jobs_db": total_jobs_db,
        "valid_data_jobs": valid_data_jobs,
        "n_data": n_data,
        "n_noise": n_noise,
        "n_base": n_base,
        "tier12_ids": tier12_ids,
        "n_fit": n_fit,
        "first_date": first_date,
        "now_str": now_str,
        "roles_counter": roles_counter,
        "top_roles": top_roles,
        "salaries_by_role": salaries_by_role,
        "n_with_salary": len(jobs_with_salary),
        "global_salary_median": global_salary_median,
        "sources": sorted(set(j.source.capitalize() for j in valid_data_jobs)),
    }


def render_scope_dashboard(data: dict, is_usd: bool = False):
    """Renderiza el dashboard completo para un ámbito de mercado específico."""
    curr_unit = "USD/mes" if is_usd else "CLP"

    # KPIs Principales
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
            <div class="metric-card">
                <div class="metric-val">{data['n_data']}</div>
                <div class="metric-lbl">Vacantes Data & Analytics</div>
            </div>
        """, unsafe_allow_html=True)
    with c2:
        fit_pct = (data['n_fit'] / data['n_base']) * 100
        st.markdown(f"""
            <div class="metric-card">
                <div class="metric-val">{data['n_fit']} <span style="font-size:1rem; color:#a6e3a1;">({fit_pct:.1f}%)</span></div>
                <div class="metric-lbl">Fit Personal Acumulado (T1+T2)</div>
            </div>
        """, unsafe_allow_html=True)
    with c3:
        tot_rem = sum(1 for j in data['valid_data_jobs'] if "remoto 100%" in extract_detailed_modality(j.location, j.description, j.job_type).lower())
        rem_pct = (tot_rem / data['n_base']) * 100
        st.markdown(f"""
            <div class="metric-card">
                <div class="metric-val">{rem_pct:.0f}%</div>
                <div class="metric-lbl">Ofertas 100% Remotas</div>
            </div>
        """, unsafe_allow_html=True)
    with c4:
        sal_str = f"${data['global_salary_median']:,.0f} {curr_unit}" if data['global_salary_median'] else "N/D"
        st.markdown(f"""
            <div class="metric-card">
                <div class="metric-val" style="color:#f9e2af;">{sal_str}</div>
                <div class="metric-lbl">Mediana Salarial Observada</div>
            </div>
        """, unsafe_allow_html=True)

    st.caption("ℹ️ *Nota de referencia:* Las vacantes, tecnologías, modalidades y bandas salariales son 100% objetivas de mercado. La columna **Fit Personal** muestra cuántas de estas vacantes hacen match con tu CV.")

    # -------------------------------------------------------------
    # SECCIÓN 1: Demanda por Rol
    # -------------------------------------------------------------
    st.subheader("1. Demanda de Mercado por Rol")

    role_rows = []
    for role, count in data['roles_counter'].most_common():
        pct = (count / data['n_base']) * 100
        r_jobs = [j for j in data['valid_data_jobs'] if normalize_role(j.title) == role]
        t12 = sum(1 for j in r_jobs if j.id in data['tier12_ids'])
        role_rows.append({
            "Rol / Perfil": role,
            "N° Vacantes Mercado": count,
            "% Mercado": f"{pct:.1f}%",
            "Fit Personal (Tier 1+2)": t12
        })

    df_roles = pd.DataFrame(role_rows)

    col_t1, col_g1 = st.columns([1.2, 1])

    with col_t1:
        st.dataframe(df_roles, width="stretch", hide_index=True)

    with col_g1:
        df_chart = pd.DataFrame({
            "Rol": [r["Rol / Perfil"] for r in role_rows],
            "Ofertas": [r["N° Vacantes Mercado"] for r in role_rows]
        }).set_index("Rol")
        st.bar_chart(df_chart, color="#cba6f7", horizontal=True)

    st.markdown("---")

    # -------------------------------------------------------------
    # SECCIÓN 2: Penetración Tecnológica por Rol
    # -------------------------------------------------------------
    st.subheader("2. Matriz de Penetración Tecnológica por Rol")
    st.caption("Frecuencia de mención de herramientas técnicas por tipo de perfil de datos e inteligencia artificial.")

    tech_patterns = get_tech_patterns()
    preferred_roles = [
        "AI & LLM Engineer",
        "Machine Learning / MLOps Engineer",
        "Data Engineer",
        "Data Analyst & BI Specialist",
        "Analytics Engineer",
        "Data Scientist",
        "Data Governance & Quality",
        "Data Architect",
        "Other Data & Analytics",
    ]

    active_roles = [r for r in preferred_roles if r in data['roles_counter']]

    tech_rows = []
    for tech, pattern in tech_patterns.items():
        total_tech_count = sum(1 for j in data['valid_data_jobs'] if re.search(pattern, f"{j.title} {j.description}".lower()))
        if total_tech_count == 0:
            continue

        row_dict = {"Herramienta / Stack": tech}
        for role in active_roles:
            r_jobs = [j for j in data['valid_data_jobs'] if normalize_role(j.title) == role]
            r_total = len(r_jobs)
            if r_total > 0:
                r_tech = sum(1 for j in r_jobs if re.search(pattern, f"{j.title} {j.description}".lower()))
                r_pct = (r_tech / r_total) * 100
                row_dict[f"{role} (n={r_total})"] = f"{r_tech} ({r_pct:.0f}%)"
            else:
                row_dict[f"{role} (n=0)"] = "0 (0%)"

        global_pct = (total_tech_count / data['n_base']) * 100
        row_dict[f"Global (n={data['n_data']})"] = f"{total_tech_count} ({global_pct:.1f}%)"
        row_dict["_total_count"] = total_tech_count
        tech_rows.append(row_dict)

    tech_rows.sort(key=lambda x: x["_total_count"], reverse=True)

    for r in tech_rows:
        del r["_total_count"]

    df_tech = pd.DataFrame(tech_rows)
    st.dataframe(df_tech, width="stretch", hide_index=True)

    st.markdown("---")

    # -------------------------------------------------------------
    # SECCIÓN 3: Modalidad de Trabajo por Rol
    # -------------------------------------------------------------
    st.subheader("3. Modalidad de Trabajo por Rol")

    mod_rows = []
    tot_rem, tot_hib, tot_pre, tot_ne = 0, 0, 0, 0

    for role in active_roles:
        r_jobs = [j for j in data['valid_data_jobs'] if normalize_role(j.title) == role]
        rt = len(r_jobs)
        if rt == 0:
            continue

        c_rem, c_hib, c_pre, c_ne = 0, 0, 0, 0
        for j in r_jobs:
            m = extract_detailed_modality(j.location, j.description, j.job_type).lower()
            if "remoto 100%" in m: c_rem += 1
            elif "híbrido" in m or "hibrido" in m: c_hib += 1
            elif "presencial 100%" in m: c_pre += 1
            else: c_ne += 1

        tot_rem += c_rem; tot_hib += c_hib; tot_pre += c_pre; tot_ne += c_ne

        mod_rows.append({
            "Rol": role,
            "Remoto 100%": f"{c_rem} ({c_rem/rt*100:.0f}%)",
            "Híbrido": f"{c_hib} ({c_hib/rt*100:.0f}%)",
            "Presencial 100%": f"{c_pre} ({c_pre/rt*100:.0f}%)",
            "No especificado": f"{c_ne} ({c_ne/rt*100:.0f}%)",
            "Total": rt,
            "% Remoto": f"{c_rem/rt*100:.0f}%"
        })

    mod_rows.append({
        "Rol": "TOTAL SEGMENTO",
        "Remoto 100%": f"{tot_rem} ({tot_rem/data['n_base']*100:.0f}%)",
        "Híbrido": f"{tot_hib} ({tot_hib/data['n_base']*100:.0f}%)",
        "Presencial 100%": f"{tot_pre} ({tot_pre/data['n_base']*100:.0f}%)",
        "No especificado": f"{tot_ne} ({tot_ne/data['n_base']*100:.0f}%)",
        "Total": data['n_data'],
        "% Remoto": f"{tot_rem/data['n_base']*100:.0f}%"
    })

    df_mod = pd.DataFrame(mod_rows)
    st.dataframe(df_mod, width="stretch", hide_index=True)

    st.markdown("---")

    # -------------------------------------------------------------
    # SECCIÓN 4: Salarios Reales Capturados
    # -------------------------------------------------------------
    st.subheader("4. Bandas Salariales Reales Capturadas")
    st.caption(f"Datos salariales 100% factuales extraídos desde avisos (Unidad: {curr_unit}). Cobertura: {data['n_with_salary']} ofertas con salario de {data['n_data']} ({(data['n_with_salary']/data['n_base'])*100:.1f}%). Esta estadística incluye todas las ofertas que publicaron salario, sin filtrar por fit de CV.")

    if data['salaries_by_role']:
        sal_rows = []
        for role, samples in data['salaries_by_role'].items():
            mins = [s["min_val"] for s in samples]
            avgs = sorted([s["avg_val"] for s in samples])
            maxs = [s["max_val"] for s in samples]
            n_samples = len(samples)
            med = avgs[n_samples // 2]
            p75 = avgs[int(n_samples * 0.75)] if n_samples >= 2 else med
            row_dict = {
                "Perfil / Cargo": role,
                "Muestras (n)": n_samples,
                f"Mínimo {curr_unit}": f"${min(mins):,.0f}",
                f"Mediana (P50) {curr_unit}": f"${med:,.0f}",
                f"Target Senior (P75) {curr_unit}": f"${p75:,.0f}",
                f"Máximo {curr_unit}": f"${max(maxs):,.0f}",
                "_med_val": med
            }
            if is_usd:
                clp_avgs = sorted([s["equiv_clp"] for s in samples])
                med_clp = clp_avgs[n_samples // 2]
                row_dict["Equiv. Mediana CLP"] = f"${med_clp:,.0f} CLP"

            sal_rows.append(row_dict)

        sal_rows.sort(key=lambda x: x["_med_val"], reverse=True)

        # Tarjeta destacada para cargos de IA
        ai_sals = [r for r in sal_rows if "AI" in r["Perfil / Cargo"] or "Machine" in r["Perfil / Cargo"]]
        if ai_sals:
            top_ai = ai_sals[0]
            st.markdown(
                f"""
                <div style="background-color: #181825; border-left: 4px solid #89dceb; border-radius: 8px; padding: 14px 18px; margin: 15px 0 20px 0;">
                    <span style="font-size: 1.05rem;">🤖 <strong style="color: #89dceb;">Destacado Perfiles de IA:</strong></span>
                    <span style="color: #cdd6f4;"> El cargo <strong style="color: #cba6f7;">{top_ai['Perfil / Cargo']}</strong> registra una Mediana (P50) de <strong style="color: #f9e2af;">{top_ai[f'Mediana (P50) {curr_unit}']}</strong> y un Target Senior (P75) de <strong style="color: #a6e3a1;">{top_ai[f'Target Senior (P75) {curr_unit}']}</strong>.</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        df_sal = pd.DataFrame(sal_rows)
        if "_med_val" in df_sal.columns:
            df_sal = df_sal.drop(columns=["_med_val"])
        st.dataframe(df_sal, width="stretch", hide_index=True)
    else:
        st.info("Sin datos salariales explícitos capturados en este segmento.")

    st.markdown("---")

    # -------------------------------------------------------------
    # SECCIÓN 5: Nota Metodológica
    # -------------------------------------------------------------
    with st.expander("📌 Nota Metodológica y Fuentes"):
        st.markdown(f"""
        - **Fuentes de datos:** {', '.join(data['sources'])} (scraping automatizado de portales de empleo).
        - **Periodo cubierto:** {data['first_date']} al {data['now_str']}.
        - **Universo en BD:** {data['total_jobs_db']} registros brutos ({data['n_data']} Data & Analytics; {data['n_noise']} ruido no-TI descartado).
        - **Normalización:** Clasificación por patrones configurados dinámicamente en `config/config.yaml`.
        - **Salarios:** Extraídos de campos estructurados o regex. Rango de validación: $600.000–$12.000.000 CLP/mes (o equivalente USD).
        - **Fit personal (Tier 1+2):** Evaluación LLM del perfil del candidato. Es un dato personal de referencia.
        """)


def main():
    st.title("📊 Estudio de Mercado Laboral: Data & Analytics")
    st.caption("Inteligencia de mercado factual sobre ofertas de empleo capturadas.")

    st.info(
        "💡 **Estudio de Mercado Objetivo:** Este informe procesa todas las ofertas de empleo capturadas "
        "de forma factual y 100% agnóstica a tu CV. La columna de **Fit Personal (Tier 1+2)** se incluye "
        "como capa de referencia complementaria para mostrar qué porcentaje del mercado se alinea con tu perfil actual."
    )

    tab_chile, tab_intl = st.tabs([
        "🇨🇱 Mercado Local Chile (Empresas Nacionales)",
        "🌎 Mercado Internacional / LATAM (Remoto USD)",
    ])

    with tab_chile:
        data_chile = load_data(scope_filter="chile")
        if data_chile:
            render_scope_dashboard(data_chile, is_usd=False)
        else:
            st.warning("No hay datos de vacantes para Chile.")

    with tab_intl:
        data_intl = load_data(scope_filter="international")
        if data_intl:
            render_scope_dashboard(data_intl, is_usd=True)
        else:
            st.warning("No hay datos de vacantes internacionales.")


if __name__ == "__main__":
    main()
