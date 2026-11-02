Quiero crear un agente encargue de realizar la configuración opentext content management para la demo de un cliente


Genera un JSON que contenga la siguiente información
	- Sector
		- Business Workspace identificados
			- Metadatos
			-Estructura de carpetas

Por ejemplo el JSON podría ser similar a este:

```JSON
{
  "catalogName": "OpenText Content Management Demo Catalog",
  "version": "1.0",
  "verticals": [
    {
      "priority": 1,
      "id": "manufacturing",
      "name": "Manufacturing / Industrial",
      "businessWorkspaces": [
        {
          "id": "product",
          "name": "Producto",
          "metadata": [
            { "name": "productId", "label": "ID Producto", "type": "string" },
            { "name": "productFamily", "label": "Familia", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Diseño e Ingeniería",
            "02_Especificaciones",
            "03_Calidad y Certificaciones",
            "04_Manuales y Documentación",
            "05_Cambios de Ingeniería"
          ]
        },
        {
          "id": "supplier",
          "name": "Proveedor",
          "metadata": [
            { "name": "supplierId", "label": "ID Proveedor", "type": "string" },
            { "name": "country", "label": "País", "type": "string" },
            { "name": "category", "label": "Categoría", "type": "string" }
          ],
          "folderTree": [
            "01_Contratos",
            "02_Homologación",
            "03_Certificaciones",
            "04_Ofertas y Tarifas",
            "05_Evaluaciones"
          ]
        },
        {
          "id": "manufacturing-order",
          "name": "Orden de fabricación",
          "metadata": [
            { "name": "orderNumber", "label": "Nº Orden", "type": "string" },
            { "name": "plant", "label": "Planta", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Orden",
            "02_Instrucciones de Trabajo",
            "03_Materiales",
            "04_Control de Calidad",
            "05_Cierre"
          ]
        }
      ]
    },
    {
      "priority": 2,
      "id": "energy-utilities",
      "name": "Energy & Utilities",
      "businessWorkspaces": [
        {
          "id": "asset",
          "name": "Activo / Instalación",
          "metadata": [
            { "name": "assetId", "label": "ID Activo", "type": "string" },
            { "name": "assetType", "label": "Tipo de activo", "type": "string" },
            { "name": "location", "label": "Localización", "type": "string" }
          ],
          "folderTree": [
            "01_Documentación Técnica",
            "02_Planos",
            "03_Operación",
            "04_Mantenimiento",
            "05_Inspecciones y Certificados"
          ]
        },
        {
          "id": "engineering-project",
          "name": "Proyecto de ingeniería",
          "metadata": [
            { "name": "projectId", "label": "ID Proyecto", "type": "string" },
            { "name": "projectType", "label": "Tipo", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Gestión del Proyecto",
            "02_Ingeniería",
            "03_Planos",
            "04_Contratistas",
            "05_Entregables"
          ]
        },
        {
          "id": "maintenance-order",
          "name": "Orden de mantenimiento",
          "metadata": [
            { "name": "orderNumber", "label": "Nº Orden", "type": "string" },
            { "name": "assetId", "label": "Activo", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Solicitud",
            "02_Diagnóstico",
            "03_Intervención",
            "04_Evidencias",
            "05_Cierre"
          ]
        }
      ]
    },
    {
      "priority": 3,
      "id": "financial-services",
      "name": "Financial Services / Banking",
      "businessWorkspaces": [
        {
          "id": "customer",
          "name": "Cliente",
          "metadata": [
            { "name": "customerId", "label": "ID Cliente", "type": "string" },
            { "name": "segment", "label": "Segmento", "type": "string" },
            { "name": "country", "label": "País", "type": "string" }
          ],
          "folderTree": [
            "01_Identificación",
            "02_KYC",
            "03_Contratos",
            "04_Correspondencia",
            "05_Compliance"
          ]
        },
        {
          "id": "credit-file",
          "name": "Expediente de crédito",
          "metadata": [
            { "name": "caseNumber", "label": "Nº Expediente", "type": "string" },
            { "name": "financingType", "label": "Tipo de financiación", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Solicitud",
            "02_Documentación Financiera",
            "03_Análisis de Riesgo",
            "04_Aprobaciones",
            "05_Formalización"
          ]
        },
        {
          "id": "contract",
          "name": "Contrato",
          "metadata": [
            { "name": "contractNumber", "label": "Nº Contrato", "type": "string" },
            { "name": "contractType", "label": "Tipo", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Borradores",
            "02_Revisión",
            "03_Aprobaciones",
            "04_Contrato Firmado",
            "05_Anexos"
          ]
        }
      ]
    },
    {
      "priority": 4,
      "id": "life-sciences",
      "name": "Life Sciences / Pharma",
      "businessWorkspaces": [
        {
          "id": "medicinal-product",
          "name": "Producto / Medicamento",
          "metadata": [
            { "name": "productId", "label": "ID Producto", "type": "string" },
            { "name": "therapeuticArea", "label": "Área terapéutica", "type": "string" },
            { "name": "regulatoryStatus", "label": "Estado regulatorio", "type": "string" }
          ],
          "folderTree": [
            "01_Desarrollo",
            "02_Calidad",
            "03_Regulatorio",
            "04_Fabricación",
            "05_Documentación de Producto"
          ]
        },
        {
          "id": "clinical-study",
          "name": "Estudio / Ensayo",
          "metadata": [
            { "name": "studyId", "label": "ID Estudio", "type": "string" },
            { "name": "phase", "label": "Fase", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Protocolo",
            "02_Aprobaciones",
            "03_Centros",
            "04_Resultados",
            "05_Informes"
          ]
        },
        {
          "id": "capa",
          "name": "Calidad / CAPA",
          "metadata": [
            { "name": "capaId", "label": "ID CAPA", "type": "string" },
            { "name": "capaType", "label": "Tipo", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Incidencia",
            "02_Investigación",
            "03_Acciones Correctivas",
            "04_Evidencias",
            "05_Cierre"
          ]
        }
      ]
    },
    {
      "priority": 5,
      "id": "public-sector",
      "name": "Public Sector / Government",
      "businessWorkspaces": [
        {
          "id": "citizen-organization",
          "name": "Ciudadano / Organización",
          "metadata": [
            { "name": "partyId", "label": "ID Interesado", "type": "string" },
            { "name": "partyType", "label": "Tipo", "type": "string" },
            { "name": "location", "label": "Localidad", "type": "string" }
          ],
          "folderTree": [
            "01_Identificación",
            "02_Solicitudes",
            "03_Comunicaciones",
            "04_Resoluciones",
            "05_Otros Documentos"
          ]
        },
        {
          "id": "administrative-case",
          "name": "Expediente administrativo",
          "metadata": [
            { "name": "caseNumber", "label": "Nº Expediente", "type": "string" },
            { "name": "procedure", "label": "Procedimiento", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Solicitud",
            "02_Documentación",
            "03_Informes",
            "04_Tramitación",
            "05_Resolución"
          ]
        },
        {
          "id": "public-procurement",
          "name": "Contrato / Licitación",
          "metadata": [
            { "name": "caseNumber", "label": "Nº Expediente", "type": "string" },
            { "name": "organization", "label": "Organismo", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Pliegos",
            "02_Ofertas",
            "03_Evaluación",
            "04_Adjudicación",
            "05_Contrato"
          ]
        }
      ]
    },
    {
      "priority": 6,
      "id": "retail",
      "name": "Retail / Consumer Goods",
      "businessWorkspaces": [
        {
          "id": "retail-product",
          "name": "Producto",
          "metadata": [
            { "name": "sku", "label": "SKU", "type": "string" },
            { "name": "category", "label": "Categoría", "type": "string" },
            { "name": "status", "label": "Estado", "type": "string" }
          ],
          "folderTree": [
            "01_Especificaciones",
            "02_Imágenes y Marketing",
            "03_Certificaciones",
            "04_Proveedores",
            "05_Documentación Comercial"
          ]
        },
        {
          "id": "retail-supplier",
          "name": "Proveedor",
          "metadata": [
            { "name": "supplierId", "label": "ID Proveedor", "type": "string" },
            { "name": "category", "label": "Categoría", "type": "string" },
            { "name": "country", "label": "País", "type": "string" }
          ],
          "folderTree": [
            "01_Contratos",
            "02_Homologación",
            "03_Catálogos",
            "04_Tarifas",
            "05_Evaluaciones"
          ]
        },
        {
          "id": "store",
          "name": "Tienda",
          "metadata": [
            { "name": "storeId", "label": "ID Tienda", "type": "string" },
            { "name": "region", "label": "Región", "type": "string" },
            { "name": "storeType", "label": "Tipo", "type": "string" }
          ],
          "folderTree": [
            "01_Operaciones",
            "02_Personal",
            "03_Instalaciones",
            "04_Compliance",
            "05_Incidencias"
          ]
        }
      ]
    }
  ]
}
```		