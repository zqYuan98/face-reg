package com.bonuos.face.dto;

import lombok.Data;

/** A person's row as the search needs it: identity, scope, revision and one feature. */
@Data
public class FeatureRow {
    private Long id;
    private Long groupId;
    private Integer status;
    private Long syncVersion;
    private byte[] featureBlob;
    private String photoUrl;
}
