#pragma once
#include "libOTe/version.h"

#define LIBOTE_VERSION (LIBOTE_VERSION_MAJOR * 10000 + LIBOTE_VERSION_MINOR * 100 + LIBOTE_VERSION_PATCH)

// build the library bit poly mul integration
/* #undef ENABLE_BITPOLYMUL */

// build the library with "simplest" Base OT enabled
#define ENABLE_SIMPLESTOT ON

// build the library with the ASM "simplest" Base OT enabled
#define ENABLE_SIMPLESTOT_ASM ON

// build the library with POPF Base OT using Ristretto KA enabled
#define ENABLE_MRR ON

// build the library with POPF Base OT using Moeller KA enabled
#define ENABLE_MRR_TWIST ON

// build the library with Masney Rindal Base OT enabled
#define ENABLE_MR ON

// build the library with Masney Rindal Kyber Base OT enabled
#define ENABLE_MR_KYBER ON

// build the library with mocked Base OT enabled
/* #undef ENABLE_MOCK_OT */


// build the library with Keller Orse Scholl OT-Ext enabled
#define ENABLE_KOS true

// build the library with IKNP OT-Ext enabled
#define ENABLE_IKNP ON

// build the library with Silent OT Extension enabled
#define ENABLE_SILENTOT ON

// build the library with SoftSpokenOT enabled
#define ENABLE_SOFTSPOKEN_OT ON

// build the library with Foleage enabled
#define ENABLE_FOLEAGE ON

// build the library with regular dpf enabled
#define ENABLE_REGULAR_DPF true

// build the library with ternary dpf enabled
#define ENABLE_TERNARY_DPF true

// build the library with sparse dpf enabled
#define ENABLE_SPARSE_DPF ON




// build the library with KOS Delta-OT-ext enabled
#define ENABLE_DELTA_KOS ON

// build the library with OOS 1-oo-N OT-Ext enabled
#define ENABLE_OOS ON

// build the library with KKRT 1-oo-N OT-Ext enabled
#define ENABLE_KKRT ON

// build the library with silent vole enabled
#define ENABLE_SILENT_VOLE ON

#define ENABLE_PPRF true

// build the library with silver codes.
/* #undef ENABLE_INSECURE_SILVER */

/* #undef ENABLE_LDPC */

// build the library with no KOS security warning
/* #undef NO_KOS_WARNING */

#if defined(ENABLE_SIMPLESTOT_ASM) && defined(_MSC_VER)
    #undef ENABLE_SIMPLESTOT_ASM
    #pragma message("ENABLE_SIMPLESTOT_ASM should not be defined on windows.")
#endif
#if defined(ENABLE_MR_KYBER) && defined(_MSC_VER)
    #undef ENABLE_MR_KYBER
    #pragma message("ENABLE_MR_KYBER should not be defined on windows.")
#endif
        
