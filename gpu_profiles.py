"""Perfis medidos: objetivos e metas térmicas, nunca clocks de outro modelo."""
PROFILES={
    'performance':{'label':'Máximo desempenho','objective':'performance','thermal_margin':0},
    'balanced':{'label':'Equilibrado','objective':'efficiency','thermal_margin':3},
    'cool':{'label':'Menos calor / ruído','objective':'efficiency','thermal_margin':7},
}

def profile(value):
    if value not in PROFILES: raise ValueError('Perfil de GPU inválido')
    return PROFILES[value]
