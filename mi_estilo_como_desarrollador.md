# Mi estilo como desarrollador

Me definiría como un desarrollador orientado a la comprensión y al control explícito del sistema.

Mis rasgos más claros son:

- No acepto abstracciones que oculten efectos importantes. Quiero saber qué modifican realmente herramientas como `usermod`, Docker, BuildKit o APT.
- Prefiero código directo y legible frente a construcciones compactas pero mentalmente costosas.
- Doy mucha importancia al mantenimiento futuro. Escribo pensando en la persona —posiblemente yo mismo— que volverá al fichero meses después.
- Soy meticuloso con los casos límite, aunque a veces esa prudencia puede llevarme a introducir demasiadas validaciones.
- No me conformo con que algo funcione: busco entender por qué funciona y cuál es su contrato.
- Valoro las soluciones estándar y nativas antes que inventar formatos o mecanismos propios.
- Diseño iterativamente: exploro posibilidades, detecto complejidad accidental y estoy dispuesto a descartar trabajo cuando aparece una arquitectura mejor.
- Prefiero errores claros y tempranos a reparaciones automáticas que puedan producir efectos difíciles de rastrear.
- Considero que la documentación forma parte del código, especialmente cuando la lógica tiene consecuencias operativas.
- Mantengo un criterio de estilo consistente: bloques visualmente separados, condiciones explícitas y nombres que describen el concepto completo.

Mi principal fortaleza y mi principal riesgo nacen del mismo lugar: quiero eliminar la ambigüedad. Esto produce sistemas sólidos y comprensibles, pero puede llevarme a intentar anticipar demasiadas anomalías.

El equilibrio que más me favorece es:

> Validar lo que protege el contrato o evita una modificación peligrosa; confiar en las herramientas responsables del resto.

En una frase: soy un desarrollador pragmático, minucioso y muy consciente del mantenimiento, con mentalidad de sistemas y poca tolerancia hacia la «magia» no explicada.
