# Tax_Austria_Calculator
**ETF TAX Calculator AUSTRIA**

Data provided by https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f?isin=

Calculation is made in the following way :
$$
\begin{align*}
\text{Capital\_Gain} &= \text{Österreichische\_KESt} \times \text{\#Shares} \\
\text{Taxable\_ETF\_Gain} &= \text{Value\_Year\_After} - \text{Value\_Year\_Before}  \\
\text{Tax\_Paid} &=  \frac {\text{Capital\_Gain}}{\text{USDEUR}} \\
\text{Percentage\_Tax\_Paid} &= \frac {\text{Österreichische KESt} \times 100}{\text{Taxable\_ETF\_Gain} \times \text{USDEUR}} \\
\text{ETF\_New\_Average\_Cost} &= \text{ETF\_Initial\_Cost} + ({\text{Fondsergebnis der Meldeperiode}} \times {\text{USDEUR}})
\end{align*}
$$

Where:


* {Österreichische KESt} is the value provided by the Fund to Öekb (USD).
    * Ertragsteuerliche Behandlung (12. Österreichische KESt, die durch Steuerabzug erhoben wird)
* Value_Year_After  (USD) is the ETF value at the date when KESt is provided.
* Value_Year_Before (USD) is the ETF value one year before.
* Fondsergebnis der Meldeperiode (USD) is the value to add to the original ETF Cost (when you bought the shares)
    * Ertragsteuerliche Behandlung (1. Fondsergebnis der Meldeperiode) 
* USDEUR is computed at the date when KESt is provided.
    * https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html
