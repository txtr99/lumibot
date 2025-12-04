inputs:
	Period( 5 ) [DisplayName = "Period", ToolTip = "Period"];
;

variables:  
	ROC( 0 ) ;

ROC = SQ_ROC( Period ) ;
 
Plot1( ROC, !("ROC" ));

